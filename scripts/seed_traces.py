#!/usr/bin/env python3
"""Seed the Agent Stream with realistic sessions across different customers.

For each scenario this creates three sessions, one per customer:
  - two sessions with a single Normal-mode turn (different customers)
  - one session with a Normal turn followed by a Demo turn (the third customer)
The customer with the mixed session rotates from one scenario to the next.

Usage:
  python scripts/seed_traces.py [--dry-run] [ScenarioId ...]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aussiecare import store
from aussiecare.agent import run_turn
from aussiecare.config import default_destinations, format_destinations
from aussiecare.context import current_customer_id, current_scenario
from aussiecare.scenarios import GOLDEN, PROMPT_INJECTION, SCENARIO_IDS, get_scenario
from aussiecare.tracing import start_conversation_session, traced_turn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("seed_traces")


def plan(scenario_ids: list[str], customer_ids: list[str]) -> list[tuple[str, str, list[str]]]:
    """(scenario, customer, modes) for every session to create."""
    out = []
    for i, scenario_id in enumerate(scenario_ids):
        mixed = customer_ids[i % len(customer_ids)]
        for customer_id in customer_ids:
            out.append((scenario_id, customer_id, ["normal", "demo"] if customer_id == mixed else ["normal"]))
    return out


def prompt_for(scenario_id: str, customer: dict, all_customers: list[dict]) -> str:
    text = get_scenario(scenario_id).sample_prompt
    motor = next((p for p in customer["policies"] if p["type"] == "motor"), customer["policies"][0])
    text = text.replace("{policy_id}", motor["policy_id"])
    # The PII prompt asks about "Liam Chen"; when Liam is the customer, ask about someone else.
    if customer["name"] == "Liam Chen":
        other = next(c["name"] for c in all_customers if c["id"] != customer["id"])
        text = text.replace("Liam Chen", other)
    return text


def seed_turn(scenario_id: str, mode: str, text: str, sessions: dict, customer: dict) -> None:
    scenario = get_scenario(scenario_id)
    behaviour = scenario_id if mode == "demo" else GOLDEN
    current_scenario.set(behaviour)
    with traced_turn(
        scenario=get_scenario(behaviour),
        user_text=text,
        policy_id=store.extract_policy_id(text),
        destinations=default_destinations(),
        sessions=sessions,
        mode=mode,
        trace_name=scenario.trace_name,
    ) as callbacks:
        answer = run_turn(behaviour, text, callbacks=callbacks, mode=mode, customer=customer)
    log.info("  done [%s] %s (%s chars)", mode, scenario.trace_name, len(answer))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    # PromptInjection has no Demo-mode failure, so it is only seeded when named explicitly.
    scenario_ids = args or [s for s in SCENARIO_IDS if s not in (GOLDEN, PROMPT_INJECTION)]
    customers = [store.get_customer(c["id"]) for c in store.list_customers()]
    by_id = {c["id"]: c for c in customers}
    dests = default_destinations()
    log.info("Destinations: %s", format_destinations(dests))

    for scenario_id, customer_id, modes in plan(scenario_ids, [c["id"] for c in customers]):
        customer = by_id[customer_id]
        text = prompt_for(scenario_id, customer, customers)
        log.info("Session: %s | %s | modes: %s", customer["name"], scenario_id, "+".join(modes))
        if dry_run:
            log.info("  prompt: %s", text)
            continue
        current_customer_id.set(customer_id)
        sessions = start_conversation_session(
            get_scenario(GOLDEN), None, destinations=dests, customer_name=customer["name"]
        )
        for mode in modes:
            seed_turn(scenario_id, mode, text, sessions, customer)


if __name__ == "__main__":
    main()
