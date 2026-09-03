"""
agents/evaluator.py — Evaluator Agent

Independently scores every agent output before it reaches the user.
Benchmarked against Databricks Genie's self-evaluation loop.

Evaluation dimensions:
  sql_correctness  — did the SQL actually answer the question?
  completeness     — are all requested data points present?
  hallucination    — did the agent invent facts not in the schema/data?
  safety           — any PII or policy violations in the output?
  confidence       — agent's own stated confidence level

Decision:
  score >= 80  → pass → return to user
  50–79        → pass with caveat (add warning to response)
  < 50         → retry with GPT-4o (upgrade from mini) + evaluator feedback
  retried < 50 → pass anyway but flag "low confidence"

Max 1 retry to control cost.
"""

from __future__ import annotations
import json, re, urllib.request, urllib.error
from pathlib import Path
from dataclasses import dataclass, field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from core.rbac import log_api_cost, calc_cost


@dataclass
class EvalResult:
    score: int                          # 0–100 aggregate
    passed: bool                        # score >= 50
    needs_retry: bool                   # score < 50
    dimensions: dict[str, int]          # per-dimension scores
    issues: list[str]                   # identified problems
    suggestions: list[str]              # how to fix them
    evaluator_note: str                 # short human-readable verdict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


# ──────────────────────────────────────────────────────────────
# LLM call
# ──────────────────────────────────────────────────────────────

def _call_evaluator(system: str, user_msg: str,
                    session_id: str, user_id: str, role: str) -> tuple[str, int, int]:
    api_key = config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    payload = json.dumps({
        "model": config.ROUTER_MODEL,   # gpt-4o-mini — evaluator is cheap
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        "max_tokens": 512,
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"OpenAI {e.code}: {body[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")

    text  = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


# ──────────────────────────────────────────────────────────────
# SQL-specific evaluation
# ──────────────────────────────────────────────────────────────

def _validate_sql_structure(sql: str) -> list[str]:
    """Rule-based SQL checks (no LLM, always runs)."""
    issues: list[str] = []
    if not sql:
        return ["No SQL was generated"]
    sql_up = sql.upper()
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
        issues.append("SQL does not start with SELECT or WITH")
    for kw in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE"]:
        if re.search(rf"\b{kw}\b", sql_up):
            issues.append(f"Dangerous keyword '{kw}' found in SQL")
    if "SELECT *" in sql_up and "LIMIT" not in sql_up:
        issues.append("SELECT * without LIMIT may return too many rows")
    return issues


# ──────────────────────────────────────────────────────────────
# Main evaluator
# ──────────────────────────────────────────────────────────────

EVAL_SYSTEM = """You are an expert AI output evaluator at MetLife.
Score the agent output on each dimension 0–100. Be strict.

Return ONLY valid JSON, no markdown:
{
  "sql_correctness": 0-100,
  "completeness":    0-100,
  "hallucination":   0-100,
  "safety":          0-100,
  "issues":          ["list of specific problems found"],
  "suggestions":     ["list of concrete improvements"],
  "note":            "one-sentence verdict"
}

Scoring guidance:
  sql_correctness: Does the SQL correctly answer the question? Does it use the right tables/columns?
  completeness:    Does the output cover everything the user asked for?
  hallucination:   100 = no invented facts. Deduct for: columns that don't exist, table names not in schema, fabricated statistics.
  safety:          100 = no PII exposed to wrong role, no policy violations.

If no SQL in output, set sql_correctness = 0."""


def evaluate(
    question: str,
    agent_name: str,
    agent_output: dict,
    schema_context: str,
    role: str,
    session_id: str,
    user_id: str,
) -> EvalResult:
    """
    Evaluate one agent output.
    Always runs rule-based SQL checks first (free).
    Then calls GPT-4o-mini for semantic evaluation.
    """
    # ── Rule-based SQL checks ──────────────────────────────────
    rule_issues: list[str] = []
    if agent_name == "text_to_sql":
        sql = agent_output.get("sql", "")
        rule_issues = _validate_sql_structure(sql)
        qr = agent_output.get("query_result", {})
        if isinstance(qr, dict) and "error" in qr:
            rule_issues.append(f"SQL execution error: {qr['error']}")

    # ── LLM evaluation ────────────────────────────────────────
    output_summary = json.dumps({
        k: v for k, v in agent_output.items()
        if k not in ("query_result",)  # exclude raw data (too long)
    }, default=str)[:1200]

    # Include result shape
    qr = agent_output.get("query_result", {})
    result_shape = ""
    if isinstance(qr, dict):
        if "error" in qr:
            result_shape = f"SQL execution failed: {qr['error']}"
        elif "data" in qr:
            result_shape = f"Query returned {qr.get('row_count', 0)} rows, columns: {qr.get('columns', [])}"

    user_msg = f"""Question asked by user: {question}

Agent: {agent_name}
Role of user: {role}

Agent output summary:
{output_summary}

SQL result shape: {result_shape}

Relevant schema (abbreviated):
{schema_context[:800]}

Rule-based issues already found: {rule_issues}

Evaluate the output quality."""

    pt, ct = 0, 0
    try:
        raw, pt, ct = _call_evaluator(EVAL_SYSTEM, user_msg, session_id, user_id, role)
        cost = calc_cost(config.ROUTER_MODEL, pt, ct)
        log_api_cost(session_id, user_id, role, "evaluator", config.ROUTER_MODEL,
                     pt, ct, cost, question[:100])

        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        ev = json.loads(cleaned)
    except Exception as e:
        # If evaluator itself fails, pass with warning
        ev = {
            "sql_correctness": 70, "completeness": 70,
            "hallucination": 80,   "safety": 95,
            "issues": [f"Evaluator error: {e}"],
            "suggestions": [],
            "note": "Evaluator failed — output not independently verified.",
        }

    # Merge rule-based issues
    ev.setdefault("issues", [])
    ev["issues"] = rule_issues + ev["issues"]

    # Aggregate score (weighted)
    dims = {
        "sql_correctness": ev.get("sql_correctness", 75),
        "completeness":    ev.get("completeness",    75),
        "hallucination":   ev.get("hallucination",   85),
        "safety":          ev.get("safety",          95),
    }
    # SQL correctness doesn't apply to non-SQL agents
    if agent_name != "text_to_sql":
        dims.pop("sql_correctness")

    weights = {
        "sql_correctness": 0.35,
        "completeness":    0.30,
        "hallucination":   0.25,
        "safety":          0.10,
    } if agent_name == "text_to_sql" else {
        "completeness":    0.50,
        "hallucination":   0.35,
        "safety":          0.15,
    }

    score = int(sum(dims[k] * weights[k] for k in dims))

    return EvalResult(
        score          = score,
        passed         = score >= 50,
        needs_retry    = score < 50,
        dimensions     = dims,
        issues         = ev.get("issues", []),
        suggestions    = ev.get("suggestions", []),
        evaluator_note = ev.get("note", ""),
        prompt_tokens  = pt,
        completion_tokens = ct,
        cost_usd       = calc_cost(config.ROUTER_MODEL, pt, ct),
    )
