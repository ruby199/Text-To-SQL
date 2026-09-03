"""
agents/pipeline.py — ONDOL Multi-Agent Pipeline

Orchestrates the full flow:
  1. Security gates (RBAC + content filter) — in app.py before this
  2. Supervisor (GPT-4o)  → build execution plan
  3. Schema Discovery (GPT-4o-mini) → understand the data
  4. Specialist Agent(s) (GPT-4o-mini) → execute plan steps
  5. Evaluator (GPT-4o-mini) → score output quality
  6. [Retry with GPT-4o if score < 50]
  7. Output gate (PII mask + audit + cost log) — in app.py after this

This is the single entry point called by app.py /api/ask.
"""

from __future__ import annotations
import time
from typing import Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from core.rbac import ROLES, log_audit, log_conversation, calc_cost
from agents.supervisor import build_plan, merge_results
from agents.agents import AGENT_MAP
from agents.evaluator import evaluate, EvalResult
from agents.schema_discovery import get_schema_context

# RBAC: which agents each role can access
AGENT_ROLE_GATE = {
    "text_to_sql":     ["IT Admin","Architect","Security Ops","Infra Engineer","Data Analyst","IT Staff"],
    "arch_review":     ["IT Admin","Architect"],
    "access_request":  ["IT Admin","Security Ops"],
    "infra_ops":       ["IT Admin","Infra Engineer"],
    "security_triage": ["IT Admin","Security Ops"],
}


class PipelineResult:
    """Structured output from the full pipeline."""
    def __init__(self):
        self.agent: str = ""
        self.agents_used: list[str] = []
        self.is_multi_step: bool = False
        self.plan_reasoning: str = ""
        self.plan_complexity: str = "simple"
        self.result: dict = {}
        self.merged_text: str = ""      # only for multi-step
        self.eval_score: int = 0
        self.eval_passed: bool = True
        self.eval_issues: list[str] = []
        self.eval_note: str = ""
        self.retried: bool = False
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.elapsed_ms: int = 0
        self.trace: list[dict] = []     # step-by-step trace for transparency

    def add_trace(self, step: str, detail: str, tokens: int = 0, cost: float = 0.0):
        self.trace.append({
            "step": step, "detail": detail,
            "tokens": tokens, "cost_usd": round(cost, 6),
        })
        self.total_tokens += tokens
        self.total_cost_usd += cost

    def to_dict(self) -> dict:
        return {
            "agent":           self.agent,
            "agents_used":     self.agents_used,
            "is_multi_step":   self.is_multi_step,
            "plan_reasoning":  self.plan_reasoning,
            "plan_complexity": self.plan_complexity,
            "result":          self.result,
            "merged_text":     self.merged_text,
            "eval": {
                "score":   self.eval_score,
                "passed":  self.eval_passed,
                "issues":  self.eval_issues,
                "note":    self.eval_note,
                "retried": self.retried,
            },
            "total_tokens":    self.total_tokens,
            "total_cost_usd":  round(self.total_cost_usd, 6),
            "elapsed_ms":      self.elapsed_ms,
            "trace":           self.trace,
        }


def run(
    question: str,
    role: str,
    user_id: str,
    session_id: str,
    turn: int,
    history: list[dict] | None = None,
) -> PipelineResult:
    """
    Execute the full ONDOL multi-agent pipeline.

    Args:
        question   : user's natural language question (PII already stripped)
        role       : RBAC role ("IT Admin", "Architect", etc.)
        user_id    : for audit logging
        session_id : for audit logging + cost grouping
        turn       : conversation turn number
        history    : last N conversation turns for multi-turn context

    Returns:
        PipelineResult with full output, eval score, trace, and cost
    """
    t0 = time.time()
    pr = PipelineResult()

    # ── Determine allowed agents for this role ─────────────────
    allowed_agents = [
        ag for ag, roles in AGENT_ROLE_GATE.items()
        if role in roles
    ]
    role_cfg = ROLES.get(role, ROLES["IT Staff"])
    allowed_tables = role_cfg["tables"]

    # ────────────────────────────────────────────────────────
    # STEP 1: SUPERVISOR — Build execution plan
    # ────────────────────────────────────────────────────────
    try:
        plan = build_plan(
            question=question,
            role=role,
            allowed_agents=allowed_agents,
            session_id=session_id,
            user_id=user_id,
            history=history,
        )
        pr.plan_reasoning = plan.reasoning
        pr.plan_complexity = plan.complexity
        pr.is_multi_step  = plan.is_multi_step
        pr.add_trace(
            "supervisor",
            f"Plan: {len(plan.steps)} step(s), complexity={plan.complexity}. {plan.reasoning}",
            tokens=plan.prompt_tokens + plan.completion_tokens,
            cost=plan.cost_usd,
        )
    except RuntimeError as e:
        raise   # propagate API key / network errors immediately

    # ────────────────────────────────────────────────────────
    # STEP 2: SCHEMA DISCOVERY (only for data-related plans)
    # ────────────────────────────────────────────────────────
    schema_context_str = ""
    has_sql_step = any(s["agent"] == "text_to_sql" for s in plan.steps)
    if has_sql_step:
        try:
            schema_ctx = get_schema_context(
                config.DB_PATH, allowed_tables, question,
                use_llm_profile=True,
            )
            schema_context_str = schema_ctx["schema_text"]
            profile = schema_ctx.get("data_profile", "")
            pr.add_trace(
                "schema_discovery",
                f"Profiled {len(schema_ctx['snapshot'])} tables. "
                f"Relevant: {schema_ctx['relevant_tables']}. "
                + (f"Profile: {profile[:120]}..." if profile else ""),
            )
        except Exception as e:
            pr.add_trace("schema_discovery", f"Warning: {e}")

    # ────────────────────────────────────────────────────────
    # STEP 3: EXECUTE PLAN STEPS
    # (parallel steps would be run concurrently — simplified to sequential here
    #  for SQLite thread safety; production would use asyncio / ThreadPoolExecutor)
    # ────────────────────────────────────────────────────────
    step_results: list[dict] = []
    eval_result: EvalResult | None = None

    for step in plan.steps:
        agent_name   = step["agent"]
        sub_question = step.get("sub_question", question)

        if agent_name not in allowed_agents:
            pr.add_trace(agent_name, f"RBAC blocked: role '{role}' cannot use this agent")
            continue

        pr.agents_used.append(agent_name)
        if not pr.agent:
            pr.agent = agent_name   # primary agent label

        agent_fn = AGENT_MAP.get(agent_name)
        if not agent_fn:
            continue

        # ── Execute specialist agent (GPT-4o-mini) ────────────
        try:
            if agent_name == "text_to_sql":
                result = agent_fn(
                    sub_question, session_id, user_id, role,
                    history=history,
                    allowed_tables=allowed_tables,
                )
            else:
                result = agent_fn(
                    sub_question, session_id, user_id, role,
                    history=history,
                )
        except RuntimeError:
            raise

        pr.add_trace(
            agent_name,
            f"Model={result.get('model_used', config.ROUTER_MODEL)} "
            f"tokens={result.get('prompt_tokens',0)+result.get('completion_tokens',0)} "
            f"cost=${result.get('cost_usd',0):.5f}",
            tokens=result.get("prompt_tokens", 0) + result.get("completion_tokens", 0),
            cost=result.get("cost_usd", 0.0),
        )

        # ── STEP 4: EVALUATOR ─────────────────────────────────
        try:
            eval_result = evaluate(
                question=sub_question,
                agent_name=agent_name,
                agent_output=result,
                schema_context=schema_context_str[:600],
                role=role,
                session_id=session_id,
                user_id=user_id,
            )
            pr.add_trace(
                "evaluator",
                f"Score={eval_result.score}/100. "
                f"Dimensions={eval_result.dimensions}. "
                f"Issues={eval_result.issues[:2]}",
                tokens=eval_result.prompt_tokens + eval_result.completion_tokens,
                cost=eval_result.cost_usd,
            )
        except Exception as e:
            pr.add_trace("evaluator", f"Evaluator error (skipped): {e}")
            eval_result = None

        # ── STEP 5: RETRY with GPT-4o if score < 50 ──────────
        if eval_result and eval_result.needs_retry and not pr.retried:
            pr.retried = True
            feedback = (
                f"Previous attempt scored {eval_result.score}/100. "
                f"Issues: {eval_result.issues}. "
                f"Suggestions: {eval_result.suggestions}. "
                f"Please fix these issues in your new response."
            )
            retry_question = f"{sub_question}\n\n[EVALUATOR FEEDBACK]: {feedback}"
            pr.add_trace("retry_upgrade", f"Upgrading to {config.AGENT_MODEL} for retry")
            try:
                if agent_name == "text_to_sql":
                    result = agent_fn(
                        retry_question, session_id, user_id, role,
                        history=history,
                        allowed_tables=allowed_tables,
                        force_model=config.AGENT_MODEL,   # GPT-4o on retry
                    )
                else:
                    result = agent_fn(
                        retry_question, session_id, user_id, role,
                        history=history,
                        force_model=config.AGENT_MODEL,
                    )
                pr.add_trace(
                    f"{agent_name}_retry",
                    f"Retry with {config.AGENT_MODEL}: "
                    f"tokens={result.get('prompt_tokens',0)+result.get('completion_tokens',0)}",
                    tokens=result.get("prompt_tokens",0)+result.get("completion_tokens",0),
                    cost=result.get("cost_usd", 0.0),
                )
            except Exception as e:
                pr.add_trace("retry_error", str(e))

        step_results.append({
            "step_id": step["step_id"],
            "agent": agent_name,
            "result": result,
        })

    # ────────────────────────────────────────────────────────
    # STEP 6: MERGE (multi-step only)
    # ────────────────────────────────────────────────────────
    if pr.is_multi_step and len(step_results) > 1:
        try:
            merged, pt, ct = merge_results(
                question, step_results, session_id, user_id, role
            )
            pr.merged_text = merged
            pr.add_trace("supervisor_merge",
                         f"Merged {len(step_results)} results",
                         tokens=pt+ct, cost=calc_cost(config.AGENT_MODEL, pt, ct))
        except Exception as e:
            pr.add_trace("merge_error", str(e))

    # ── Final result ──────────────────────────────────────────
    pr.result = step_results[0]["result"] if step_results else {}

    if eval_result:
        pr.eval_score  = eval_result.score
        pr.eval_passed = eval_result.passed
        pr.eval_issues = eval_result.issues
        pr.eval_note   = eval_result.evaluator_note

    pr.elapsed_ms = int((time.time() - t0) * 1000)

    # ── IFS-KR compliance: log conversation ───────────────────
    summary = pr.result.get("summary", pr.result.get("explanation", ""))
    log_conversation(session_id, user_id, role, turn, "assistant", str(summary), pr.agent)
    log_audit(session_id, user_id, role, "PIPELINE",
              f"agents={pr.agents_used} eval={pr.eval_score} "
              f"cost=${pr.total_cost_usd:.5f} retried={pr.retried}")

    return pr
