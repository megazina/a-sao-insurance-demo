"""AussieCare customer portal + assistant API (FastAPI)."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aussiecare import store
from aussiecare.agent import run_turn
from aussiecare.config import (
    DEST_LABELS,
    PROVIDER_LABELS,
    configured_destinations,
    configured_providers,
    default_destinations,
    default_provider,
)
from aussiecare.context import current_customer_id, current_scenario
from aussiecare.prompts import classify_intent
from aussiecare.scenarios import GOLDEN, SCENARIOS, get_scenario
from aussiecare.tracing import resume_sessions, start_conversation_session, traced_turn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aussiecare.server")

WEB_DIR = Path(__file__).parent / "web"
COOKIE = "ac_sid"

app = FastAPI(title="AussieCare")
# sid -> {"customer_id": str, "sao": {dest: session_id}}  (one Splunk session per login and destination, shared by both modes)
SESSIONS: dict[str, dict] = {}

DEMO_TRIGGERS = [
    {"label": "Hallucination: wrong kangaroo excess", "normal_label": "Hit kangaroo excess", "prompt": SCENARIOS["Hallucination"].sample_prompt},
    {"label": "Skipped check: hire car", "normal_label": "Hire car", "prompt": "Can you book me an emergency hire car on {policy_id}?"},
    {"label": "Emergency tone: tree through roof", "normal_label": "Tree through the roof", "prompt": SCENARIOS["Emergency"].sample_prompt},
    {"label": "PII leak: TFN / other customer", "normal_label": "TFN / other customer", "prompt": SCENARIOS["PiiLeak"].sample_prompt},
]


class LoginBody(BaseModel):
    customer_id: str


class ChatBody(BaseModel):
    message: str
    mode: str = "normal"
    provider: str | None = None
    destinations: list[str] | None = None  # None = server default; [] = tracing off


def _session(sid: str | None) -> dict:
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=401, detail="Not signed in")
    return SESSIONS[sid]


@app.get("/api/users")
def users():
    return store.list_customers()


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    try:
        store.get_customer(body.customer_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown customer")
    sid = secrets.token_hex(4) + secrets.token_hex(4)
    SESSIONS[sid] = {"customer_id": body.customer_id, "sao": {}}
    response.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response, ac_sid: str | None = Cookie(default=None)):
    SESSIONS.pop(ac_sid or "", None)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(ac_sid: str | None = Cookie(default=None)):
    sess = _session(ac_sid)
    return {**store.public_customer(sess["customer_id"]), "session": ac_sid[:8]}


@app.get("/api/config")
def config():
    providers = configured_providers()
    return {
        "providers": [{"id": p, "label": PROVIDER_LABELS[p]} for p in providers],
        "default_provider": default_provider(),
        "demo_triggers": DEMO_TRIGGERS,
        "destinations": [
            {"id": d, "label": DEST_LABELS[d], "default": d in default_destinations()}
            for d in configured_destinations()
        ],
    }


@app.post("/api/reset")
def reset():
    store.reset()
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatBody, ac_sid: str | None = Cookie(default=None)):
    sess = _session(ac_sid)
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    providers = configured_providers()
    if not providers:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider configured. Set OPENAI_API_KEY or AWS credentials in .env.",
        )
    provider = body.provider if body.provider in providers else default_provider()
    mode = "demo" if body.mode == "demo" else "normal"
    intent = classify_intent(text)
    scenario_id = intent if mode == "demo" else GOLDEN
    scenario = get_scenario(scenario_id)
    intent_scenario = get_scenario(intent)

    customer_id = sess["customer_id"]
    customer = store.get_customer(customer_id)
    policy_id = store.extract_policy_id(text) or (customer["policies"][0]["policy_id"])
    available = configured_destinations()
    dests = default_destinations() if body.destinations is None else [d for d in body.destinations if d in available]

    cid_token = current_customer_id.set(customer_id)
    scn_token = current_scenario.set(scenario_id)
    try:
        # One Splunk session per login and destination, shared by both modes.
        missing = [d for d in dests if d not in sess["sao"]]
        if missing:
            sess["sao"].update(
                start_conversation_session(
                    get_scenario(GOLDEN),
                    policy_id,
                    destinations=missing,
                    mode=mode,
                    customer_name=customer["name"],
                )
            )
        sessions = {d: sess["sao"][d] for d in dests if d in sess["sao"]}
        resume_sessions(sessions)
        trace_name = intent_scenario.trace_name if intent != GOLDEN else f"Turn · {text[:48]}"
        with traced_turn(
            scenario=scenario,
            user_text=text,
            policy_id=policy_id,
            destinations=dests,
            sessions=sessions,
            mode=mode,
            trace_name=trace_name,
        ) as callbacks:
            answer = run_turn(scenario_id, text, provider, callbacks, mode, customer)
    except Exception as exc:
        log.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"Assistant error: {exc}")
    finally:
        current_customer_id.reset(cid_token)
        current_scenario.reset(scn_token)
    return {"answer": answer, "mode": mode, "scenario": scenario_id, "provider": provider}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
