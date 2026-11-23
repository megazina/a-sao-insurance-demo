"""Named sessions, traces, and typed spans for a readable webinar waterfall."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

from aussiecare.config import (
    AGENT_STREAM,
    PROJECT_NAME,
    configured_destinations,
    credentials_for,
    dest_env_keys,
    sao_is_configured,
)
from aussiecare.scenarios import Scenario

logger = logging.getLogger(__name__)


def workflow_span_name(mode: str) -> str:
    return f"workflow_{mode}"


def _patch_span_names() -> None:
    """Show workflow spans as their own name and LLM spans as "LLM".

    splunk-ao 0.7 names exported spans "<operation> <detail>" (for example
    "invoke_workflow AussieCare Copilot" and "chat <model>"). This overrides only
    the span *name*; attributes such as the model id are untouched. It relies on
    a private function in the SDK, so it fails soft if that changes.
    """
    try:
        from galileo_core.schemas.logging.step import StepType
        from splunk_ao.converter import span_converter
    except Exception:
        logger.debug("splunk-ao span converter not available; span names unchanged", exc_info=True)
        return
    original = getattr(span_converter, "_span_name", None)
    if original is None or getattr(original, "_aussiecare_patched", False):
        return

    def _span_name(span, step_type):
        if step_type is StepType.workflow and getattr(span, "name", None):
            return str(span.name)
        if step_type is StepType.llm:
            return LLM_DISPLAY_NAME
        return original(span, step_type)

    _span_name._aussiecare_patched = True  # type: ignore[attr-defined]
    span_converter._span_name = _span_name

    # Also expose mode / scenario as flat span attributes (the SDK only exports
    # them inside the splunk_ao.metadata JSON string), so they are easy to see
    # and filter on at trace level.
    build = getattr(span_converter, "build_span_attributes", None)
    if build is not None and not getattr(build, "_aussiecare_patched", False):

        def build_span_attributes(span, *args, **kwargs):
            attrs = build(span, *args, **kwargs)
            meta = getattr(span, "user_metadata", None) or {}
            for key in ("mode", "scenario"):
                if meta.get(key):
                    attrs[f"aussiecare.{key}"] = str(meta[key])
            return attrs

        build_span_attributes._aussiecare_patched = True  # type: ignore[attr-defined]
        span_converter.build_span_attributes = build_span_attributes

    # Splunk shows the root workflow span as the trace and drops its own custom
    # attributes, so also put mode / scenario on the OTel *resource*, the other
    # place a backend can read trace-wide values from.
    converter_cls = getattr(span_converter, "SpanConverter", None)
    convert = getattr(converter_cls, "convert_span", None)
    if convert is not None and not getattr(convert, "_aussiecare_patched", False):
        from opentelemetry.sdk.resources import Resource

        def convert_span(self, span, span_context, parent_span_context, session_id, resource, end_time_ns=None):
            meta = getattr(span, "user_metadata", None) or {}
            extra = {f"aussiecare.{k}": str(meta[k]) for k in ("mode", "scenario") if meta.get(k)}
            if extra:
                resource = resource.merge(Resource(extra))
            return convert(self, span, span_context, parent_span_context, session_id, resource, end_time_ns)

        convert_span._aussiecare_patched = True  # type: ignore[attr-defined]
        converter_cls.convert_span = convert_span


_patch_span_names()

WORKFLOW_NAME = "AussieCare Copilot"  # legacy; the workflow span is now workflow_<mode>
LLM_DISPLAY_NAME = "LLM"
AGENT_NAME = "AussieCare Copilot"
# OpenAI rejects message.name values with spaces: ^[^\s<|\\/>]+$
GRAPH_AGENT_NAME = "AussieCare_Copilot"
LLM_SPAN_NAME = "claims_reasoner"
RETRIEVER_SPAN_NAME = "retrieve_pds"

_SAO_LOGGERS: dict[str, Any] = {}
_SAO_LOCK = threading.Lock()
_GALILEO_BRIDGE_KEYS = (
    "GALILEO_API_KEY",
    "GALILEO_API_URL",
    "GALILEO_CONSOLE_URL",
    "GALILEO_PROJECT",
    "GALILEO_PROJECT_ID",
    "GALILEO_LOG_STREAM",
    "GALILEO_LOG_STREAM_ID",
    "GALILEO_JWT_TOKEN",
    "GALILEO_SSO_ID_TOKEN",
    "GALILEO_SSO_PROVIDER",
    "GALILEO_USERNAME",
    "GALILEO_PASSWORD",
    "GALILEO_MODE",
    "GALILEO_HOME_DIR",
)
_active_destinations: ContextVar[tuple[str, ...]] = ContextVar("sao_destinations", default=())
_active_sessions: ContextVar[dict[str, str]] = ContextVar("sao_sessions", default={})


class _PinSessionCallback(BaseCallbackHandler):
    """Forward LangChain events to one destination after pinning its session."""

    run_inline = True

    def __init__(self, inner: Any, sao: Any, session_id: str | None) -> None:
        super().__init__()
        self._inner = inner
        self._sao = sao
        self._session_id = session_id

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("on_"):
            inner = object.__getattribute__(self, "_inner")
            sao = object.__getattribute__(self, "_sao")
            session_id = object.__getattribute__(self, "_session_id")
            target = getattr(inner, name)

            def bound(*args: Any, **kwargs: Any) -> Any:
                if session_id:
                    sao.set_session(session_id=session_id)
                return target(*args, **kwargs)

            return bound
        return object.__getattribute__(self, name)


def _detach_sdk_config() -> None:
    """Drop the SDK config singleton without resetting live logger configs."""
    try:
        from splunk_ao.config import SplunkAOConfig

        SplunkAOConfig._instance = None
    except Exception:
        logger.debug("Could not detach SplunkAOConfig singleton", exc_info=True)


def _clear_galileo_bridge() -> None:
    for key in _GALILEO_BRIDGE_KEYS:
        os.environ.pop(key, None)


def _inject_dest_env(dest: str) -> dict[str, str | None]:
    saved = {key: os.environ.get(key) for key in dest_env_keys()}
    for key in dest_env_keys():
        os.environ.pop(key, None)
    _clear_galileo_bridge()
    os.environ.update(credentials_for(dest))
    _detach_sdk_config()
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for key in dest_env_keys():
        os.environ.pop(key, None)
    _clear_galileo_bridge()
    _detach_sdk_config()
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value


def get_sao_logger(dest: str):
    """One logger per destination. Env is isolated so SaaS and on-prem can coexist."""
    if dest not in configured_destinations():
        return None
    with _SAO_LOCK:
        cached = _SAO_LOGGERS.get(dest)
        if cached is not None:
            return cached
        saved = _inject_dest_env(dest)
        try:
            from splunk_ao import SplunkAOLogger

            sao = SplunkAOLogger(project=PROJECT_NAME, agent_stream=AGENT_STREAM)
            # Capture the dest-specific API client now; session CRUD later
            # would otherwise hit SplunkAOConfig.get() with mixed or empty env.
            sao._ensure_session_crud_client()
            _SAO_LOGGERS[dest] = sao
        except Exception:
            logger.exception("Failed to initialise Splunk AO logger for %s", dest)
            return None
        finally:
            _restore_env(saved)
        return _SAO_LOGGERS[dest]


def start_conversation_session(
    scenario: Scenario,
    policy_id: str | None,
    destinations: Sequence[str] | None = None,
    mode: str = "normal",
    customer_name: str | None = None,
) -> dict[str, str]:
    dests = list(destinations) if destinations is not None else configured_destinations()
    if not dests:
        logger.warning("No Splunk AO destinations selected; traces will not be sent.")
        return {}
    policy = policy_id or "unknown-policy"
    who = customer_name or policy
    name = f"AussieCare · {who}"
    metadata = {"policy_id": policy, "product": "AussieCare"}
    sessions: dict[str, str] = {}
    for dest in dests:
        sao = get_sao_logger(dest)
        if sao is None:
            continue
        try:
            sessions[dest] = sao.start_session(name=name, metadata=metadata)
        except Exception:
            logger.exception("Failed to start Splunk AO session for %s", dest)
    return sessions


def resume_sessions(sessions: dict[str, str] | None) -> None:
    if not sessions or not sao_is_configured():
        return
    for dest, session_id in sessions.items():
        sao = get_sao_logger(dest)
        if sao is not None and session_id:
            sao.set_session(session_id=session_id)


def resume_session(session_id: str | None) -> None:
    """Back-compat for a single session id (uses the first configured destination)."""
    if not session_id:
        return
    dests = configured_destinations()
    if dests:
        resume_sessions({dests[0]: session_id})


@contextmanager
def traced_turn(
    *,
    scenario: Scenario,
    user_text: str,
    policy_id: str | None,
    destinations: Sequence[str] | None = None,
    sessions: dict[str, str] | None = None,
    mode: str = "normal",
    trace_name: str | None = None,
) -> Iterator[list[Any]]:
    """One user turn = one named trace per selected destination."""
    chosen = configured_destinations() if destinations is None else destinations
    dests = [d for d in chosen if d in configured_destinations()]
    if not dests:
        yield []
        return

    try:
        from splunk_ao.handlers.langchain import SplunkAOCallback
    except ImportError:
        from splunk_ao.handlers.langchain.handler import SplunkAOCallback

    tags = [f"scenario:{scenario.id}", f"mode:{mode}"]
    metadata = {
        "scenario": scenario.id,
        "mode": mode,
        "policy_id": policy_id or "",
        "incident_type": scenario.session_hint,
    }
    session_map = dict(sessions or {})
    dest_token = _active_destinations.set(tuple(dests))
    session_token = _active_sessions.set(session_map)
    callbacks: list[Any] = []
    loggers: list[Any] = []
    try:
        for dest in dests:
            sao = get_sao_logger(dest)
            if sao is None:
                continue
            session_id = session_map.get(dest)
            if session_id:
                sao.set_session(session_id=session_id)
            sao.start_trace(
                input=user_text,
                name=trace_name or scenario.trace_name,
                tags=tags,
                metadata=metadata,
            )
            sao.add_workflow_span(
                input=user_text, name=workflow_span_name(mode), tags=tags, metadata=metadata
            )
            inner = SplunkAOCallback(
                splunk_ao_logger=sao,
                start_new_trace=False,
                flush_on_chain_end=False,
            )
            callbacks.append(_PinSessionCallback(inner, sao, session_id))
            loggers.append(sao)
        try:
            yield callbacks
        finally:
            for sao in loggers:
                try:
                    sao.conclude(conclude_all=True)
                except Exception:
                    logger.exception("Failed to conclude Splunk AO trace")
                try:
                    sao.flush()
                except Exception:
                    logger.exception("Failed to flush Splunk AO logger")
    finally:
        _active_destinations.reset(dest_token)
        _active_sessions.reset(session_token)


def log_retriever_span(query: str, documents: list[str]) -> None:
    sessions = _active_sessions.get()
    for dest in _active_destinations.get():
        sao = get_sao_logger(dest)
        if sao is None:
            continue
        session_id = sessions.get(dest)
        if session_id:
            sao.set_session(session_id=session_id)
        try:
            sao.add_retriever_span(
                input=query,
                output=documents,
                name=RETRIEVER_SPAN_NAME,
            )
        except Exception:
            logger.exception("Failed to log retriever span for %s", dest)
