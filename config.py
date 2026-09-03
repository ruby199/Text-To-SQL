"""
config.py — ONDOL Configuration
Loads .env file using stdlib only (no python-dotenv required).
All modules import from here — never os.environ directly.
"""
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent


# ── .env loader (pure stdlib) ─────────────────────────────────
def _load_env(path: Path) -> None:
    """Parse a .env file and populate os.environ (does not overwrite existing vars)."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            # Skip comments and blank lines
            if not line or line.startswith("#"):
                continue
            # KEY=VALUE or KEY="VALUE" or KEY='VALUE'
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Don't overwrite values already in the environment
            if key not in os.environ:
                os.environ[key] = value


# Load .env on import
_load_env(BASE_DIR / ".env")


# ── Typed config accessors ────────────────────────────────────
def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _get_float(key: str, default: float) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default

def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default

def _get_bool(key: str, default: bool) -> bool:
    return _get(key, str(default)).lower() in ("1", "true", "yes")


# ── Public config values ──────────────────────────────────────

# OpenAI
OPENAI_API_KEY:   str   = _get("OPENAI_API_KEY")
ROUTER_MODEL:     str   = _get("ROUTER_MODEL",   "gpt-4o-mini")
AGENT_MODEL:      str   = _get("AGENT_MODEL",    "gpt-4o")
MAX_TOKENS:       int   = _get_int("MAX_TOKENS_PER_CALL", 1500)

# Flask
SECRET_KEY:       str   = _get("SECRET_KEY", "ondol-dev-secret")
FLASK_PORT:       int   = _get_int("FLASK_PORT", 5001)
DEBUG:            bool  = _get_bool("DEBUG", False)

# Cost guard
MAX_COST_SESSION: float = _get_float("MAX_COST_PER_SESSION", 0.0)

# Cache
CACHE_TTL:        int   = _get_int("CACHE_TTL", 300)

# Paths
DB_PATH:          Path  = BASE_DIR / "data" / "ondol.db"


# ── Validation ────────────────────────────────────────────────
def validate() -> list[str]:
    """Return list of configuration warnings (not errors — app still starts)."""
    warnings = []
    if not OPENAI_API_KEY:
        warnings.append(
            "OPENAI_API_KEY is not set in .env or environment. "
            "AI agents will return errors. Edit .env or run:\n"
            "  export OPENAI_API_KEY='sk-...'"
        )
    if SECRET_KEY == "ondol-dev-secret":
        warnings.append("SECRET_KEY is using the default dev value. Set a strong key in .env for production.")
    return warnings


def print_status() -> None:
    """Print config status at startup."""
    key_status = f"sk-...{OPENAI_API_KEY[-4:]}" if len(OPENAI_API_KEY) > 8 else ("NOT SET ⚠" if not OPENAI_API_KEY else "set")
    print(f"  OPENAI_API_KEY : {key_status}")
    print(f"  ROUTER_MODEL   : {ROUTER_MODEL}")
    print(f"  AGENT_MODEL    : {AGENT_MODEL}")
    print(f"  MAX_TOKENS     : {MAX_TOKENS}")
    print(f"  CACHE_TTL      : {CACHE_TTL}s")
    if MAX_COST_SESSION:
        print(f"  COST LIMIT     : ${MAX_COST_SESSION:.2f} / session")
    print(f"  DB_PATH        : {DB_PATH}")
