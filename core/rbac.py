"""
core/rbac.py  —  Role-Based Access Control + IFS-KR Compliance Layer
Every query is filtered, logged, and audited before/after AI.
"""
import sqlite3, re, json, os
from datetime import datetime, date, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
import sys; sys.path.insert(0, str(_ROOT))
import config

DB = str(config.DB_PATH)

# ── Role definitions ─────────────────────────────────────────
ROLES = {
    "IT Admin": {
        "level": 99,
        "tables": ["*"],
        "pii_visible": True,
        "can_manage": True,
        "can_view_costs": True,
        "description": "Full access to all data, audit logs, and cost dashboard"
    },
    "Architect": {
        "level": 80,
        "tables": ["arb_reviews", "incidents", "infra_assets", "departments"],
        "pii_visible": False,
        "can_manage": False,
        "can_view_costs": False,
        "description": "ARB reviews, incidents, infra assets"
    },
    "Security Ops": {
        "level": 75,
        "tables": ["security_alerts", "incidents", "access_requests", "employees"],
        "pii_visible": True,
        "can_manage": False,
        "can_view_costs": False,
        "description": "Security alerts, incidents, access requests"
    },
    "Infra Engineer": {
        "level": 65,
        "tables": ["infra_assets", "incidents", "departments"],
        "pii_visible": False,
        "can_manage": False,
        "can_view_costs": True,
        "description": "Infrastructure assets and incidents"
    },
    "Data Analyst": {
        "level": 55,
        "tables": ["incidents", "security_alerts", "access_requests",
                   "arb_reviews", "infra_assets", "departments"],
        "pii_visible": False,
        "can_manage": False,
        "can_view_costs": False,
        "description": "All aggregated data (PII masked)"
    },
    "IT Staff": {
        "level": 20,
        "tables": ["incidents", "arb_reviews"],
        "pii_visible": False,
        "can_manage": False,
        "can_view_costs": False,
        "description": "Own incidents and ARB status only"
    },
}

DEMO_USERS = [
    {"id": "U001", "name": "Alex (IT Admin)",       "email": "admin@ondol.demo",   "password": "admin1",  "role": "IT Admin"},
    {"id": "U002", "name": "Blair (Architect)",     "email": "arch@ondol.demo",    "password": "arch1",   "role": "Architect"},
    {"id": "U003", "name": "Casey (Security Ops)",  "email": "sec@ondol.demo",     "password": "sec1",    "role": "Security Ops"},
    {"id": "U004", "name": "Dana (Infra Engineer)", "email": "infra@ondol.demo",   "password": "infra1",  "role": "Infra Engineer"},
    {"id": "U005", "name": "Erin (Data Analyst)",   "email": "data@ondol.demo",    "password": "data1",   "role": "Data Analyst"},
    {"id": "U006", "name": "Frank (IT Staff)",      "email": "staff@ondol.demo",   "password": "staff1",  "role": "IT Staff"},
]

# ── PII detection + masking ──────────────────────────────────
PII_PATTERNS = [
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), "[EMAIL]"),
    (re.compile(r'\b\d{3}[-.\s]?\d{4}[-.\s]?\d{4}\b'), "[PHONE]"),
    (re.compile(r'\b\d{6}[-]?\d{7}\b'), "[KR-ID]"),
    (re.compile(r'\bSYN_(?:User|Head)_\w+\b'), "[PERSON]"),   # catch our synthetic names too
]

def strip_pii(text: str) -> str:
    """Remove PII from text before sending to AI."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

def mask_result_pii(rows: list, role: str) -> list:
    """Mask PII columns in query results for non-privileged roles."""
    if ROLES.get(role, {}).get("pii_visible", False):
        return rows
    pii_cols = {"name", "email", "head", "assignee_id", "requestor_id",
                "submitter_id", "emp_id"}
    masked = []
    for row in rows:
        new = {}
        for k, v in row.items():
            if k.lower() in pii_cols and v and not str(v).startswith("["):
                new[k] = str(v)[:4] + "****"
            else:
                new[k] = v
        masked.append(new)
    return masked


# ── SQL safety gate ──────────────────────────────────────────
ALLOWED_TABLES = {
    "incidents", "access_requests", "arb_reviews", "infra_assets",
    "security_alerts", "employees", "departments", "api_cost_log"
}

def validate_sql(sql: str, role: str) -> tuple[str | None, str | None]:
    """
    Returns (clean_sql, error).
    Enforces: SELECT only, allowed tables, role-level table access.
    """
    sql = sql.strip().rstrip(";")
    # 1. Read-only check
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
        return None, "Only SELECT queries are permitted (IFS-KR read-only enforcement)."
    # 2. Block dangerous keywords
    blocked = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
               "EXEC", "EXECUTE", "PRAGMA", "ATTACH"]
    for kw in blocked:
        if re.search(rf"\b{kw}\b", sql, re.IGNORECASE):
            return None, f"Keyword '{kw}' is not permitted."
    # 3. Table access check
    perm = ROLES.get(role, ROLES["IT Staff"])
    allowed = perm["tables"]
    if allowed != ["*"]:
        found = re.findall(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z_0-9]*)', sql, re.IGNORECASE)
        forbidden = [t.lower() for t in found if t.lower() not in allowed]
        if forbidden:
            return None, f"Access denied: table(s) {forbidden} not permitted for role '{role}'."
    return sql, None


# ── Content filter (pre/post AI) ─────────────────────────────
BLOCKED_INTENTS = [
    r"ignore\s+(previous|all)\s+(instructions|prompts)",
    r"you\s+are\s+now\s+(a|an)\s+\w+",
    r"jailbreak|dan\s+mode|developer\s+mode",
    r"repeat\s+(the|your)\s+(system|prompt|instructions)",
    r"forget\s+(your|all)\s+(rules|instructions)",
]

def content_filter_input(text: str) -> tuple[bool, str]:
    """Returns (is_safe, reason). Blocks prompt injection attempts."""
    for pattern in BLOCKED_INTENTS:
        if re.search(pattern, text, re.IGNORECASE):
            return False, "Input blocked by content filter (policy violation)."
    if len(text) > 2000:
        return False, "Query too long (max 2000 chars)."
    return True, ""

def content_filter_output(text: str) -> str:
    """Post-AI output scan — strip any PII that slipped through."""
    return strip_pii(text)


# ── Audit / Compliance logging ───────────────────────────────
def _conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def log_conversation(session_id, user_id, user_role, turn, role, content, agent):
    """IFS-KR: log all conversations, retain 1 year, strip PII before storing."""
    safe_content = strip_pii(content)
    retain_until = (date.today() + timedelta(days=365)).isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = _conn()
    conn.execute(
        """INSERT INTO conversation_log
           (session_id,user_id,user_role,turn,role,content,agent,created_at,retained_until)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (session_id, user_id, user_role, turn, role, safe_content, agent, now, retain_until)
    )
    conn.commit(); conn.close()

def log_audit(session_id, user_id, user_role, action, detail, ip="0.0.0.0"):
    conn = _conn()
    conn.execute(
        "INSERT INTO audit_log (session_id,user_id,user_role,action,detail,ip_address,created_at) VALUES (?,?,?,?,?,?,?)",
        (session_id, user_id, user_role, action, detail, ip, datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit(); conn.close()

def log_api_cost(session_id, user_id, user_role, agent, model,
                 prompt_t, completion_t, cost, query):
    conn = _conn()
    conn.execute(
        """INSERT INTO api_cost_log
           (session_id,user_id,user_role,agent,model,prompt_tokens,completion_tokens,cost_usd,query_text,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_id, user_id, user_role, agent, model,
         prompt_t, completion_t, cost, strip_pii(query),
         datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit(); conn.close()


# ── Auth helpers ─────────────────────────────────────────────
def authenticate(email: str, password: str):
    for u in DEMO_USERS:
        if u["email"] == email and u["password"] == password:
            return {k: u[k] for k in ("id", "name", "email", "role")}
    return None

def get_role(role_name: str) -> dict:
    return ROLES.get(role_name, ROLES["IT Staff"])


# ── Cost calculation ─────────────────────────────────────────
# Source: platform.openai.com/docs/pricing  (verified May 2026)
# Prices per 1K tokens
MODEL_PRICES = {
    # OpenAI — flagship
    "gpt-4o":               {"in": 0.0025,  "out": 0.01000, "label": "GPT-4o"},
    "gpt-4o-2024-11-20":    {"in": 0.0025,  "out": 0.01000, "label": "GPT-4o Nov"},
    # OpenAI — mini (router + evaluator + specialists)
    "gpt-4o-mini":          {"in": 0.000150,"out": 0.000600, "label": "GPT-4o-mini"},
    "gpt-4o-mini-2024-07-18":{"in":0.000150,"out": 0.000600, "label": "GPT-4o-mini"},
    # OpenAI — GPT-4.1 family (newer)
    "gpt-4.1":              {"in": 0.002,   "out": 0.008,   "label": "GPT-4.1"},
    "gpt-4.1-mini":         {"in": 0.0004,  "out": 0.0016,  "label": "GPT-4.1-mini"},
    "gpt-4.1-nano":         {"in": 0.0001,  "out": 0.0004,  "label": "GPT-4.1-nano"},
    # Anthropic (if swapped in)
    "claude-sonnet-4-5":    {"in": 0.003,   "out": 0.015,   "label": "Claude Sonnet"},
    "claude-haiku-4-5":     {"in": 0.00025, "out": 0.00125, "label": "Claude Haiku"},
}

def calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost for an API call. Falls back to gpt-4o rates if model unknown."""
    prices = MODEL_PRICES.get(model, {"in": 0.0025, "out": 0.01})
    return round(
        (prompt_tokens  / 1000) * prices["in"] +
        (completion_tokens / 1000) * prices["out"],
        8
    )

def cost_breakdown(model: str, prompt_tokens: int, completion_tokens: int) -> dict:
    """Return a detailed cost breakdown dict for display."""
    prices = MODEL_PRICES.get(model, {"in": 0.0025, "out": 0.01})
    input_cost  = round((prompt_tokens  / 1000) * prices["in"], 8)
    output_cost = round((completion_tokens / 1000) * prices["out"], 8)
    return {
        "model":           model,
        "model_label":     MODEL_PRICES.get(model, {}).get("label", model),
        "prompt_tokens":   prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens":    prompt_tokens + completion_tokens,
        "input_cost_usd":  input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd":  round(input_cost + output_cost, 8),
        "price_per_1k_in": prices["in"],
        "price_per_1k_out":prices["out"],
    }
