"""
agents/supervisor.py — Supervisor Agent

The orchestrator of the ONDOL multi-agent pipeline.
Uses GPT-4o (the most capable model) to:

  1. Understand intent deeply — simple query vs multi-step plan
  2. Decide which agent(s) to invoke and in what order
  3. Detect if the question needs multiple agents (e.g. "show cost AND triage alerts")
  4. Build an execution plan with steps
  5. After specialist output: review results + decide if retry is needed

This mirrors how Databricks Genie's AI/BI planner works — the high-level
understanding is separated from the execution to keep specialised agents focused.
"""

from __future__ import annotations
import json, re, urllib.request, urllib.error
from dataclasses import dataclass, field
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from core.rbac import log_api_cost, calc_cost

ALL_AGENTS = {
    "text_to_sql":    "Answer data questions, KPIs, metrics, trends, SQL queries",
    "arch_review":    "Draft ARB documents, check architecture standards, RFC submissions",
    "access_request": "Process AD / SailPoint access provisioning and deprovisioning",
    "infra_ops":      "VM right-sizing, cloud cost, runbooks, DR, capacity planning",
    "security_triage":"Classify security alerts, recommend SOAR playbooks, generate Splunk SPL",
}


@dataclass
class ExecutionPlan:
    steps: list[dict]           # [{ agent, sub_question, depends_on }]
    is_multi_step: bool
    reasoning: str              # why this plan was chosen
    complexity: str             # "simple" | "moderate" | "complex"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


# ──────────────────────────────────────────────────────────────
# LLM call — always GPT-4o for supervisor
# ──────────────────────────────────────────────────────────────

def _call_gpt4o(system: str, user_msg: str,
                session_id: str, user_id: str, role: str) -> tuple[str, int, int]:
    api_key = config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    payload = json.dumps({
        "model":      config.AGENT_MODEL,   # gpt-4o
        "messages":   [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        "max_tokens": 800,
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body[:300]
        raise RuntimeError(f"OpenAI supervisor error {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")

    text  = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


# ──────────────────────────────────────────────────────────────
# Plan builder
# ──────────────────────────────────────────────────────────────

PLAN_SYSTEM = """You are the Supervisor Agent of ONDOL, an enterprise AI platform.
Your job is to break down a user request into a precise execution plan.

Available specialist agents:
{agents_list}

Rules:
- Most questions require ONE agent. Only split into multiple steps if the user CLEARLY asks for two separate types of work (e.g. "show cost data AND draft an ARB").
- For data questions, ALWAYS use text_to_sql — never answer from memory.
- If you detect a multi-step need, steps that are independent can run in parallel (mark depends_on: null). Steps that need prior results must reference their dependency.
- complexity: "simple" = one agent, one call. "moderate" = one agent with context needed. "complex" = multi-agent or iterative.

Return ONLY valid JSON, no markdown:
{{
  "steps": [
    {{
      "step_id": "s1",
      "agent": "agent_name",
      "sub_question": "the exact question/instruction for this agent",
      "depends_on": null
    }}
  ],
  "is_multi_step": false,
  "reasoning": "why you chose this plan in one sentence",
  "complexity": "simple|moderate|complex"
}}"""


def build_plan(
    question: str,
    role: str,
    allowed_agents: list[str],
    session_id: str,
    user_id: str,
    history: list[dict] | None = None,
) -> ExecutionPlan:
    """
    Call GPT-4o to build an execution plan for the question.
    Falls back to a simple keyword-based plan if API is unavailable.
    """
    agents_list = "\n".join(
        f"  {name}: {desc}"
        for name, desc in ALL_AGENTS.items()
        if name in allowed_agents
    )

    system = PLAN_SYSTEM.format(agents_list=agents_list)

    # Include recent history context for follow-up detection
    history_ctx = ""
    if history:
        recent = history[-4:]
        history_ctx = "\n\nRecent conversation (for context):\n" + "\n".join(
            f"  [{m['role']}]: {m['content'][:100]}" for m in recent
        )

    user_msg = f"User question: {question}{history_ctx}\nUser role: {role}"

    try:
        raw, pt, ct = _call_gpt4o(system, user_msg, session_id, user_id, role)
        cost = calc_cost(config.AGENT_MODEL, pt, ct)
        log_api_cost(session_id, user_id, role, "supervisor", config.AGENT_MODEL,
                     pt, ct, cost, question[:100])

        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        plan_data = json.loads(cleaned)

        # Validate agent names
        for step in plan_data.get("steps", []):
            if step["agent"] not in allowed_agents:
                step["agent"] = _keyword_fallback(question, allowed_agents)

        return ExecutionPlan(
            steps         = plan_data.get("steps", []),
            is_multi_step = plan_data.get("is_multi_step", False),
            reasoning     = plan_data.get("reasoning", ""),
            complexity    = plan_data.get("complexity", "simple"),
            prompt_tokens     = pt,
            completion_tokens = ct,
            cost_usd          = cost,
        )

    except Exception:
        # Fallback: single-agent keyword plan
        agent = _keyword_fallback(question, allowed_agents)
        return ExecutionPlan(
            steps         = [{"step_id": "s1", "agent": agent,
                              "sub_question": question, "depends_on": None}],
            is_multi_step = False,
            reasoning     = "Fallback keyword routing (API unavailable or plan parse failed)",
            complexity    = "simple",
        )


def _keyword_fallback(question: str, allowed: list[str]) -> str:
    """Keyword-based agent selection when supervisor LLM is unavailable."""
    q = question.lower()
    candidates = [
        ("arch_review",    ["arb", "architecture", "review", "rfc", "standard"]),
        ("access_request", ["access", "provision", "ad group", "sailpoint", "permission"]),
        ("infra_ops",      ["vm", "infra", "cost", "runbook", "sizing", "dr", "cloud"]),
        ("security_triage",["alert", "security", "splunk", "soar", "threat", "malware"]),
        ("text_to_sql",    []),  # default
    ]
    for agent, keywords in candidates:
        if agent in allowed and any(kw in q for kw in keywords):
            return agent
    return "text_to_sql" if "text_to_sql" in allowed else allowed[0]


# ──────────────────────────────────────────────────────────────
# Result merger (for multi-step plans)
# ──────────────────────────────────────────────────────────────

MERGE_SYSTEM = """You are the Supervisor Agent finalizing a multi-step AI response.
You received results from multiple specialist agents. Combine them into a single
coherent answer for the user.

Be concise. Lead with the most important finding.
Return plain text (not JSON). Use markdown for structure."""


def merge_results(
    question: str,
    step_results: list[dict],
    session_id: str,
    user_id: str,
    role: str,
) -> tuple[str, int, int]:
    """
    For multi-step plans: ask GPT-4o to synthesise results into one answer.
    Returns (merged_text, prompt_tokens, completion_tokens).
    """
    results_text = "\n\n".join(
        f"--- Step {r['step_id']} ({r['agent']}) ---\n{json.dumps(r['result'], default=str)[:800]}"
        for r in step_results
    )
    user_msg = f"Original question: {question}\n\nAgent results:\n{results_text}"

    raw, pt, ct = _call_gpt4o(MERGE_SYSTEM, user_msg, session_id, user_id, role)
    cost = calc_cost(config.AGENT_MODEL, pt, ct)
    log_api_cost(session_id, user_id, role, "supervisor_merge", config.AGENT_MODEL,
                 pt, ct, cost, question[:100])
    return raw, pt, ct
