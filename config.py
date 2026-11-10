import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "").strip()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:4b")
# Slower reasoning model for real questions (math, code, explanations). Empty = always LLM_MODEL.
LLM_SMART_MODEL = os.getenv("LLM_SMART_MODEL", "").strip()
# Out of credits (HTTP 402): first retry with a small token cap (Hack Club AI reserves
# each request's max cost up front, so small requests still fit near the daily limit)...
LLM_CHEAP_MODEL = os.getenv("LLM_CHEAP_MODEL", LLM_MODEL).strip()  # same Opus, just a smaller cap
LLM_CHEAP_MAX_TOKENS = int(os.getenv("LLM_CHEAP_MAX_TOKENS", "3000"))
# ...then the local model. Empty = no fallback.
LLM_FALLBACK_BASE_URL = os.getenv("LLM_FALLBACK_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "").strip()
# Extra thinking for real questions on cloud models: low | medium | high (empty = off).
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "240"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4000"))
# Real questions think before answering; reasoning tokens count against this too.
LLM_MAX_TOKENS_SERIOUS = int(os.getenv("LLM_MAX_TOKENS_SERIOUS", "16000"))
# Thinking models (qwen3, deepseek-r1) emit a long chain-of-thought before the
# real answer, which costs latency and eats the token budget. Ollama honours
# "think": false; other OpenAI-compatible providers reject/ignore it.
LLM_THINK = os.getenv("LLM_THINK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _is_ollama() -> bool:
    return "11434" in LLM_BASE_URL or LLM_API_KEY.strip().lower() == "ollama"


def llm_body_extra() -> dict:
    """Provider-specific request fields merged into the chat completion body."""
    return {} if (LLM_THINK or not _is_ollama()) else {"think": False}


def reasoning_extra(serious: bool, effort: str = "") -> dict:
    effort = effort or LLM_REASONING_EFFORT
    if not serious or not effort or _is_ollama():
        return {}
    return {"reasoning": {"effort": effort}}


BOT_NAME = os.getenv("BOT_NAME", "pixol")
KEYWORDS = [k.strip().lower() for k in os.getenv("BOT_KEYWORDS", "pixol").split(",") if k.strip()]
HISTORY_FILE = BASE_DIR / "memory.json"
FACTS_FILE = BASE_DIR / "facts.json"
MAX_FACTS = int(os.getenv("MAX_FACTS", "25"))
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "10"))
MUTE_MINUTES = float(os.getenv("MUTE_MINUTES", "15"))
KEYWORD_COOLDOWN = float(os.getenv("KEYWORD_COOLDOWN", "10"))
# wait this long after a follow-up for more messages before replying once
FOLLOWUP_DEBOUNCE = float(os.getenv("FOLLOWUP_DEBOUNCE", "4"))
FOLLOWUP_HOURS = float(os.getenv("FOLLOWUP_HOURS", "12"))
CONTEXT_MSGS = int(os.getenv("CONTEXT_MSGS", "10"))
TZ = os.getenv("BOT_TZ", "Asia/Kolkata")
OWNER_ID = os.getenv("OWNER_ID", "").strip()
OWNER_NAME = os.getenv("OWNER_NAME", "Eraxty").strip()
OWNER_PRONOUNS = os.getenv("OWNER_PRONOUNS", "they/them").strip()
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "16"))
MAX_MSG_CHARS = int(os.getenv("MAX_MSG_CHARS", "600"))

# Code sandbox: one Docker container per thread (see sandbox.py)
SANDBOX_ENABLED = os.getenv("SANDBOX_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK", "none")  # "bridge" lets code pip install / hit the web
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "512m")
SANDBOX_CPUS = float(os.getenv("SANDBOX_CPUS", "1"))
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "60"))  # seconds per run
SANDBOX_OUTPUT_LIMIT = int(os.getenv("SANDBOX_OUTPUT_LIMIT", "4000"))
SANDBOX_MAX_FILE = int(os.getenv("SANDBOX_MAX_FILE", str(10_000_000)))
SANDBOX_IDLE_STOP = float(os.getenv("SANDBOX_IDLE_STOP", "900"))  # stop after 15 idle minutes
SANDBOX_TTL_HOURS = float(os.getenv("SANDBOX_TTL_HOURS", "12"))  # then delete

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()  # optional: higher API rate limit
REMINDERS_FILE = BASE_DIR / "reminders.json"
# search_slack: how far back it scans (pages of 200 messages), how many threads it opens, hits it returns
SEARCH_PAGES = int(os.getenv("SEARCH_PAGES", "10"))
SEARCH_THREADS = int(os.getenv("SEARCH_THREADS", "60"))
SEARCH_MAX_HITS = int(os.getenv("SEARCH_MAX_HITS", "40"))
STREAM_EVERY = float(os.getenv("STREAM_EVERY", "1.2"))  # seconds between live message edits

NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "25000"))
SHOT_MAX_H = int(os.getenv("SHOT_MAX_H", "2600"))
PAGE_TEXT_LIMIT = int(os.getenv("PAGE_TEXT_LIMIT", "6000"))
SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "5"))

MISSING_TOKEN_MSG = (
    "pixol needs TWO different Slack tokens, and they are not interchangeable:\n"
    "\n"
    "  1. SLACK_BOT_TOKEN  (xoxb-)  — OAuth & Permissions -> Bot Token Scopes\n"
    "     -> Install to Workspace -> 'Bot User OAuth Token'\n"
    "\n"
    "  2. SLACK_APP_TOKEN  (xapp-)  — Basic Information -> App-Level Tokens\n"
    "     -> Generate Token -> scope 'connections:write'\n"
    "     Socket Mode will not connect without this one. No OAuth flow can create it.\n"
    "\n"
    "Missing from .env: {missing}\n"
    "(see README.md)"
)

WRONG_BOT_TOKEN_MSG = (
    "SLACK_BOT_TOKEN looks like a {kind} token, but a bot token (xoxb-) is required.\n"
    "  OAuth & Permissions -> Bot Token Scopes -> Install to Workspace\n"
    "  -> copy 'Bot User OAuth Token'.\n"
    "  If you used a user OAuth flow, its access_token starts with xoxp- and acts\n"
    "  as a human, not as a bot that can be invited to a channel."
)

WRONG_APP_TOKEN_MSG = (
    "SLACK_APP_TOKEN looks like a {kind} token, but an app-level token (xapp-) is required.\n"
    "  Socket Mode needs one, and it can only be created from a registered app:\n"
    "  Basic Information -> Generate Token -> scope 'connections:write'.\n"
    "  No OAuth flow can produce this token."
)


def _kind(tok: str) -> str:
    if tok.startswith("xoxb-"):
        return "bot (xoxb-)"
    if tok.startswith("xapp-"):
        return "app-level (xapp-)"
    if tok.startswith("xoxp-"):
        return "user (xoxp-)"
    if tok.startswith("xoxe"):
        return "token-rotation (xoxe-)"
    return "unrecognised"


def validate() -> None:
    missing = [
        name
        for name, val in (("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN), ("SLACK_APP_TOKEN", SLACK_APP_TOKEN))
        if not val
    ]
    if missing:
        raise SystemExit(MISSING_TOKEN_MSG.format(missing=", ".join(missing)))
    if not SLACK_BOT_TOKEN.startswith("xoxb-"):
        raise SystemExit(WRONG_BOT_TOKEN_MSG.format(kind=_kind(SLACK_BOT_TOKEN)))
    if not SLACK_APP_TOKEN.startswith("xapp-"):
        raise SystemExit(WRONG_APP_TOKEN_MSG.format(kind=_kind(SLACK_APP_TOKEN)))
