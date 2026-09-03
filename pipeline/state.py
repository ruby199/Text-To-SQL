"""
pipeline/state.py — Pipeline State

Mirrors LangGraph's StateGraph state definition.
With real LangGraph:
    from langgraph.graph import StateGraph
    graph = StateGraph(AgentState)

Without it: we pass the same TypedDict through node functions manually.
The state shape is identical — migration to LangGraph = swap the runner only.
"""
from __future__ import annotations
from typing import Any, TypedDict
import operator


# ──────────────────────────────────────────────────────────────
# Immutable input state (set once, never mutated)
# ──────────────────────────────────────────────────────────────
class InputState(TypedDict):
    question:   str
    role:       str
    user_id:    str
    session_id: str
    turn:       int
    history:    list[dict]


# ──────────────────────────────────────────────────────────────
# Mutable pipeline state (each node reads + writes)
# Annotated fields use operator.add for LangGraph reducer compatibility
# ──────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    # ── Input (copied from InputState) ────────────────────────
    question:   str
    role:       str
    user_id:    str
    session_id: str
    turn:       int
    history:    list[dict]

    # ── Planning (supervisor output) ───────────────────────────
    plan:             dict          # {steps, is_multi_step, reasoning, complexity}
    current_step_idx: int           # which step we're executing
    allowed_agents:   list[str]     # RBAC-filtered agent names

    # ── Schema discovery ───────────────────────────────────────
    schema_text:      str           # formatted DDL + sample rows
    data_profile:     str           # GPT-4o-mini paragraph
    snapshot:         dict          # raw table → columns + samples

    # ── Semantic enrichment ────────────────────────────────────
    semantic_context: str           # glossary + metrics + join paths
    kpi_name:         str           # if a named KPI was detected

    # ── Execution ─────────────────────────────────────────────
    # Reducers: operator.add appends each step's results
    agent_results:    list[dict]    # [{step_id, agent, result}]
    step_results:     list[dict]    # alias for agent_results

    # ── Evaluation ────────────────────────────────────────────
    eval_score:       int
    eval_passed:      bool
    eval_issues:      list[str]
    eval_note:        str
    eval_dimensions:  dict          # {sql_correctness, completeness, ...}
    retry_count:      int           # how many retries so far

    # ── Thinking trace (streamed to UI) ───────────────────────
    # Each node appends its thinking steps here
    thinking_steps: list[dict]     # [{id, emoji, label, detail, tokens, cost_usd, ts}]

    # ── Merge (multi-step) ────────────────────────────────────
    merged_text:      str

    # ── Final output ──────────────────────────────────────────
    final_result:     dict
    total_cost_usd:   float
    total_tokens:     int
    elapsed_ms:       int
    pipeline_error:   str           # non-empty if pipeline failed


def make_initial_state(
    question: str,
    role: str,
    user_id: str,
    session_id: str,
    turn: int,
    history: list[dict] | None = None,
    allowed_agents: list[str] | None = None,
) -> AgentState:
    """Create a fresh AgentState for a new request."""
    return AgentState(
        question        = question,
        role            = role,
        user_id         = user_id,
        session_id      = session_id,
        turn            = turn,
        history         = history or [],
        plan            = {},
        current_step_idx= 0,
        allowed_agents  = allowed_agents or [],
        schema_text     = "",
        data_profile    = "",
        snapshot        = {},
        semantic_context= "",
        kpi_name        = "",
        agent_results   = [],
        step_results    = [],
        eval_score      = 0,
        eval_passed     = True,
        eval_issues     = [],
        eval_note       = "",
        eval_dimensions = {},
        retry_count     = 0,
        thinking_steps  = [],
        merged_text     = "",
        final_result    = {},
        total_cost_usd  = 0.0,
        total_tokens    = 0,
        elapsed_ms      = 0,
        pipeline_error  = "",
    )
