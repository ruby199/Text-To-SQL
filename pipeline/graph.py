"""
pipeline/graph.py — StateGraph Orchestrator

This is the LangGraph-pattern graph runner.

With real LangGraph, this would be:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.sqlite import SqliteSaver

    graph = StateGraph(AgentState)
    graph.add_node("supervisor",        node_supervisor)
    graph.add_node("schema_discovery",  node_schema_discovery)
    ...
    graph.add_conditional_edges("evaluator", route_after_eval, {...})
    graph.compile(checkpointer=SqliteSaver.from_conn_string("ondol.db"))

Without the library: identical logic, manual state passing.
Migration = replace the runner with compiled LangGraph graph.
"""

from __future__ import annotations
import time
from typing import Callable, Generator, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from core.rbac import log_audit, log_conversation, content_filter_output
from pipeline.state import AgentState, make_initial_state
from pipeline.loader import PipelineConfig
from pipeline.nodes import (
    node_supervisor,
    node_schema_discovery,
    node_semantic_layer,
    node_text_to_sql,
    node_specialist,
    node_evaluator,
    node_merge,
)

_cfg = PipelineConfig()

# ──────────────────────────────────────────────────────────────
# SSE EVENT EMITTER
# ──────────────────────────────────────────────────────────────
import json as _json

def _make_emitter(events: list[dict]) -> Callable:
    """
    Returns a callback that appends SSE events to a shared list.
    The SSE endpoint reads from this list and streams to the client.
    """
    def emit(event_type: str, data: Any):
        events.append({"type": event_type, "data": data})
    return emit


# ──────────────────────────────────────────────────────────────
# CONDITIONAL EDGE: route after evaluator
# (mirrors LangGraph add_conditional_edges)
# ──────────────────────────────────────────────────────────────
def _route_after_eval(eval_result: dict, retry_count: int) -> str:
    """
    Returns "pass" or "retry".
    LangGraph equivalent:
        def route(state): return "output" if state["eval_passed"] else "retry"
        graph.add_conditional_edges("evaluator", route, {"output": "output", "retry": "specialist"})
    """
    if eval_result["needs_retry"] and retry_count < _cfg.max_retries:
        return "retry"
    return "pass"


# ──────────────────────────────────────────────────────────────
# MAIN GRAPH RUNNER
# ──────────────────────────────────────────────────────────────
def run(
    question: str,
    role: str,
    user_id: str,
    session_id: str,
    turn: int,
    history: list[dict] | None = None,
    event_sink: list[dict] | None = None,   # SSE events written here
) -> AgentState:
    """
    Execute the full pipeline graph.
    event_sink: if provided, thinking steps are appended as SSE events.
    """
    t0 = time.time()

    # Determine allowed agents from YAML RBAC
    allowed_agents = _cfg.allowed_agents_for_role(role)

    # Initialise state
    state = make_initial_state(
        question       = question,
        role           = role,
        user_id        = user_id,
        session_id     = session_id,
        turn           = turn,
        history        = history or [],
        allowed_agents = allowed_agents,
    )

    # Emitter — sends thinking steps to SSE sink
    emit = _make_emitter(event_sink) if event_sink is not None else None
    if emit:
        emit("pipeline_start", {
            "question": question, "role": role,
            "allowed_agents": allowed_agents,
        })

    # ── NODE 1: SUPERVISOR ────────────────────────────────────
    state = node_supervisor(state, emit=emit)
    plan  = state["plan"]
    steps = plan.get("steps", [])
    if not steps:
        steps = [{"step_id": "s1", "agent": "text_to_sql",
                  "sub_question": question, "depends_on": None}]
        state["plan"]["steps"] = steps

    # ── NODE 2: SCHEMA DISCOVERY (if data agent in plan) ─────
    has_data_agent = any(
        _cfg.needs_schema_discovery(s["agent"]) for s in steps
    )
    if has_data_agent:
        state = node_schema_discovery(state, emit=emit)

    # ── NODE 3: SEMANTIC LAYER ────────────────────────────────
    if has_data_agent:
        state = node_semantic_layer(state, emit=emit)

    # ── NODE 4-5: EXECUTE STEPS + EVALUATE ───────────────────
    step_results: list[dict] = []

    for step in steps:
        agent_name   = step["agent"]
        sub_question = step.get("sub_question", question)
        step_id      = step["step_id"]

        if agent_name not in allowed_agents:
            if emit:
                emit("rbac_block", {"agent": agent_name, "role": role})
            continue

        # ── Execute specialist ─────────────────────────────────
        if agent_name == "text_to_sql":
            result = node_text_to_sql(
                state, sub_question, step_id,
                force_model_key="specialist", emit=emit,
            )
        else:
            result = node_specialist(
                state, agent_name, sub_question, step_id,
                force_model_key="specialist", emit=emit,
            )

        # ── Evaluator ─────────────────────────────────────────
        eval_res = node_evaluator(
            state, agent_name, result, sub_question, emit=emit,
        )
        state["eval_score"]      = eval_res["score"]
        state["eval_passed"]     = eval_res["passed"]
        state["eval_issues"]     = eval_res["issues"]
        state["eval_note"]       = eval_res["note"]
        state["eval_dimensions"] = eval_res["dimensions"]

        # ── Conditional edge: retry if score < threshold ───────
        route = _route_after_eval(eval_res, state["retry_count"])
        if route == "retry":
            state["retry_count"] += 1
            if emit:
                emit("retry_upgrade", {
                    "reason": eval_res["issues"],
                    "from_model": _cfg.resolve_model("specialist"),
                    "to_model":   _cfg.resolve_model("supervisor"),
                })
            # Retry with GPT-4o + evaluator feedback
            feedback = (
                f"Previous attempt scored {eval_res['score']}/100. "
                f"Issues: {eval_res['issues']}. "
                f"Suggestions: {eval_res['suggestions']}. "
                f"Fix these in your new response."
            )
            retry_q = f"{sub_question}\n\n[EVALUATOR FEEDBACK]: {feedback}"

            if agent_name == "text_to_sql":
                result = node_text_to_sql(
                    state, retry_q, step_id + "_retry",
                    force_model_key="supervisor",   # upgrade to GPT-4o
                    emit=emit,
                )
            else:
                result = node_specialist(
                    state, agent_name, retry_q, step_id + "_retry",
                    force_model_key="supervisor",
                    emit=emit,
                )

            # Re-evaluate after retry (quick, no threshold check)
            eval_res2 = node_evaluator(state, agent_name, result, sub_question)
            state["eval_score"]  = eval_res2["score"]
            state["eval_passed"] = eval_res2["passed"]
            state["eval_note"]   = f"[Retried] {eval_res2['note']}"

        step_results.append({"step_id": step_id, "agent": agent_name, "result": result})
        state["agent_results"].append({"step_id": step_id, "agent": agent_name, "result": result})

    # ── NODE 6: MERGE (multi-step) ────────────────────────────
    state["step_results"] = step_results
    if plan.get("is_multi_step") and len(step_results) > 1:
        state["merged_text"] = node_merge(state, step_results, emit=emit)

    # ── Final result ──────────────────────────────────────────
    state["final_result"] = (
        step_results[0]["result"] if step_results else {}
    )
    state["elapsed_ms"] = int((time.time() - t0) * 1000)

    # ── IFS-KR compliance logging ─────────────────────────────
    summary = str(state["final_result"].get(
        "summary", state["final_result"].get("explanation", "")
    ))
    summary = content_filter_output(summary)
    log_conversation(session_id, user_id, role, turn, "assistant", summary,
                     state["agent_results"][0]["agent"] if state["agent_results"] else "pipeline")
    log_audit(session_id, user_id, role, "PIPELINE",
              f"agents={[r['agent'] for r in state['agent_results']]} "
              f"eval={state['eval_score']} cost=${state['total_cost_usd']:.5f} "
              f"retried={state['retry_count'] > 0} ms={state['elapsed_ms']}")

    if emit:
        emit("pipeline_done", {
            "eval_score":    state["eval_score"],
            "total_tokens":  state["total_tokens"],
            "total_cost":    state["total_cost_usd"],
            "elapsed_ms":    state["elapsed_ms"],
        })

    return state


# ──────────────────────────────────────────────────────────────
# SSE STREAMING GENERATOR
# Used by Flask /api/ask/stream endpoint
# ──────────────────────────────────────────────────────────────
def stream(
    question: str,
    role: str,
    user_id: str,
    session_id: str,
    turn: int,
    history: list[dict] | None = None,
) -> Generator[str, None, None]:
    """
    Generator that yields SSE-formatted strings.
    Flask endpoint does:
        return Response(stream(...), mimetype="text/event-stream")
    """
    import threading

    events: list[dict] = []
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, str] = {}

    def worker():
        try:
            state = run(question, role, user_id, session_id, turn, history, event_sink=events)
            result_holder["state"] = state
        except Exception as e:
            error_holder["error"] = str(e)
            events.append({"type": "error", "data": {"message": str(e),
                           "api_key_missing": "OPENAI_API_KEY" in str(e)}})

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    sent = 0
    while t.is_alive() or sent < len(events):
        while sent < len(events):
            ev = events[sent]
            yield f"data: {_json.dumps(ev)}\n\n"
            sent += 1
        time.sleep(0.05)

    # Final event with complete state
    if "state" in result_holder:
        s = result_holder["state"]
        yield f"data: {_json.dumps({'type': 'result', 'data': _state_to_response(s)})}\n\n"

    yield "data: {\"type\": \"done\"}\n\n"


def _state_to_response(state: AgentState) -> dict:
    """Convert final state to the API response dict including per-call cost breakdown."""
    from core.rbac import cost_breakdown as _cb, MODEL_PRICES

    # Build per-agent cost breakdown from thinking steps
    agent_costs = []
    for step in state.get("thinking_steps", []):
        if step.get("tokens", 0) > 0 and step.get("cost_usd", 0) > 0:
            agent_costs.append({
                "label":    step["label"],
                "emoji":    step.get("emoji", ""),
                "tokens":   step["tokens"],
                "cost_usd": step["cost_usd"],
            })

    # Detailed cost card for Admin display
    total_cost = state.get("total_cost_usd", 0.0)
    total_tokens = state.get("total_tokens", 0)
    elapsed_s = state.get("elapsed_ms", 0) / 1000

    cost_card = {
        "total_cost_usd":    round(total_cost, 6),
        "total_tokens":      total_tokens,
        "elapsed_s":         round(elapsed_s, 2),
        "agent_breakdown":   agent_costs,
        "retried":           state.get("retry_count", 0) > 0,
        "retry_cost_usd":    round(total_cost * 0.3, 6) if state.get("retry_count", 0) > 0 else 0,
        "cost_per_1k_tokens":round(total_cost / max(total_tokens, 1) * 1000, 6),
        "model_prices_ref": {
            "gpt-4o":      "$2.50/$10.00 per 1M tokens",
            "gpt-4o-mini": "$0.15/$0.60 per 1M tokens",
        },
    }

    return {
        "agent":          state["agent_results"][0]["agent"] if state["agent_results"] else "",
        "agents_used":    [r["agent"] for r in state["agent_results"]],
        "is_multi_step":  state["plan"].get("is_multi_step", False),
        "plan_reasoning": state["plan"].get("reasoning", ""),
        "plan_complexity":state["plan"].get("complexity", "simple"),
        "result":         state["final_result"],
        "merged_text":    state["merged_text"],
        "cost_card":      cost_card,
        "pipeline": {
            "eval_score":      state["eval_score"],
            "eval_passed":     state["eval_passed"],
            "eval_issues":     state["eval_issues"],
            "eval_note":       state["eval_note"],
            "eval_dimensions": state["eval_dimensions"],
            "retried":         state.get("retry_count", 0) > 0,
            "total_cost_usd":  round(total_cost, 6),
            "total_tokens":    total_tokens,
            "elapsed_ms":      state["elapsed_ms"],
            "thinking_steps":  state["thinking_steps"],
        }
    }
