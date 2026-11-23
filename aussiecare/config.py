"""Runtime config from environment. Secrets stay in .env, never in source."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PDS_DIR = DATA_DIR / "pds"
CUSTOMERS_PATH = DATA_DIR / "customers.json"
CHROMA_DIR = REPO_ROOT / ".chroma"

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o")

_OPENAI_HTTP_CLIENT = None


def os_ssl_context():
    """TLS context that uses the OS trust store, not certifi alone.

    Homebrew Python + httpx default to certifi. That fails on networks that
    intercept HTTPS with a corporate CA in the macOS Keychain.
    """
    import ssl

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


def openai_http_client():
    """Shared httpx client for OpenAI, verified against OS CAs."""
    global _OPENAI_HTTP_CLIENT
    if _OPENAI_HTTP_CLIENT is None:
        import httpx

        _OPENAI_HTTP_CLIENT = httpx.Client(verify=os_ssl_context(), timeout=60.0)
    return _OPENAI_HTTP_CLIENT
PROJECT_NAME = os.getenv("SPLUNK_AO_PROJECT", "AussieCare")
AGENT_STREAM = os.getenv("SPLUNK_AO_AGENT_STREAM", "webinar-live")

SES_NUMBER = "132 500"
EMERGENCY_ACCOMMODATION_LINE = "1800 555 0199"

DEST_SAAS = "saas"
DEST_ONPREM = "onprem"
DEST_LABELS = {
    DEST_SAAS: "SaaS (Observability Cloud)",
    DEST_ONPREM: "On-prem",
}

_O11Y_KEYS = ("SPLUNK_AO_REALM", "SPLUNK_AO_O11Y_TOKEN", "SPLUNK_AO_O11Y_API_TOKEN")
_STANDALONE_KEYS = ("SPLUNK_AO_API_KEY", "SPLUNK_AO_CONSOLE_URL", "SPLUNK_AO_API_URL")
_DEST_KEYS = _O11Y_KEYS + _STANDALONE_KEYS

# Popped from os.environ so SplunkAOLogger does not raise AmbiguousConfigurationError
# when both SaaS and on-prem credentials are present. Values stay in this snapshot.
_CREDENTIALS: dict[str, str] = {}
_ISOLATED = False


def isolate_sao_credentials() -> None:
    """Keep both dest credential sets, but never both in os.environ at once."""
    global _ISOLATED
    if _ISOLATED:
        return
    for key in _DEST_KEYS:
        value = os.environ.pop(key, None)
        if value:
            _CREDENTIALS[key] = value
    _ISOLATED = True


isolate_sao_credentials()


def saas_configured() -> bool:
    return bool(
        _CREDENTIALS.get("SPLUNK_AO_REALM")
        and (_CREDENTIALS.get("SPLUNK_AO_O11Y_TOKEN") or _CREDENTIALS.get("SPLUNK_AO_O11Y_API_TOKEN"))
    )


def onprem_configured() -> bool:
    return bool(_CREDENTIALS.get("SPLUNK_AO_API_KEY") and _CREDENTIALS.get("SPLUNK_AO_CONSOLE_URL"))


def configured_destinations() -> list[str]:
    dests: list[str] = []
    if saas_configured():
        dests.append(DEST_SAAS)
    if onprem_configured():
        dests.append(DEST_ONPREM)
    return dests


def default_destinations() -> list[str]:
    available = configured_destinations()
    raw = os.getenv("SAO_DEFAULT_DESTINATIONS", "")
    if not raw.strip():
        return list(available)
    wanted = []
    for part in raw.split(","):
        dest = part.strip().lower()
        if dest in (DEST_SAAS, DEST_ONPREM) and dest in available and dest not in wanted:
            wanted.append(dest)
    return wanted


def dest_env_keys() -> tuple[str, ...]:
    return _DEST_KEYS


def credentials_for(dest: str) -> dict[str, str]:
    if dest == DEST_SAAS:
        keys = _O11Y_KEYS
    elif dest == DEST_ONPREM:
        keys = _STANDALONE_KEYS
    else:
        return {}
    return {key: _CREDENTIALS[key] for key in keys if key in _CREDENTIALS}


def sao_is_configured() -> bool:
    return bool(configured_destinations())


def format_destinations(dests: list[str]) -> str:
    if not dests:
        return "nowhere (tracing off)"
    return " and ".join(DEST_LABELS[d] for d in dests if d in DEST_LABELS)


# --- Model provider (OpenAI / Amazon Bedrock) ---
# These read AWS/model env vars directly, unlike the SAO destination vars above:
# isolate_sao_credentials() only exists to stop SplunkAOLogger seeing both
# destinations' credentials at once, which is unrelated to boto3/langchain_aws
# picking up AWS_* vars from the normal environment or credential chain.

PROVIDER_OPENAI = "openai"
PROVIDER_BEDROCK = "bedrock"
PROVIDER_LABELS = {
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_BEDROCK: "Amazon Bedrock",
}

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
BEDROCK_REGION = os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")


def openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def bedrock_configured() -> bool:
    if not BEDROCK_REGION:
        return False
    if os.getenv("AWS_PROFILE"):
        return True
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        return True
    return False


def configured_providers() -> list[str]:
    providers: list[str] = []
    if openai_configured():
        providers.append(PROVIDER_OPENAI)
    if bedrock_configured():
        providers.append(PROVIDER_BEDROCK)
    return providers


def default_provider() -> str | None:
    available = configured_providers()
    if not available:
        return None
    preferred = os.getenv("DEFAULT_MODEL_PROVIDER", "").strip().lower()
    if preferred in available:
        return preferred
    return available[0]
