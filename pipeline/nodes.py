"""
pipeline/nodes.py — Graph Node Functions

Each function here is one node in the LangGraph StateGraph.
With real LangGraph:
    graph.add_node("supervisor", node_supervisor)
    graph.add_node("schema_discovery", node_schema_discovery)
    ...

Each node:
  1. Reads from state
  2. Does work (LLM call, DB query, etc.)
  3. Appends to state.thinking_steps  ← streamed to UI
  4. Returns updated state fields

The `emit` callback is called for each thinking step so the SSE
endpoint can stream it to the browser in real time.
"""
from __future__ import annotations
import json, re, time, sqlite3, hashlib, urllib.request, urllib.error
from typing import Callable, Any
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from core.rbac import validate_sql, mask_result_pii, log_api_cost, calc_cost
from core.semantic_layer import build_context, KPIS, detect_kpi
from agents.schema_discovery import get_schema_context
from pipeline.state import AgentState
from pipeline.loader import PipelineConfig

# ── Global pipeline config ─────────────────────────────────────
_cfg = PipelineConfig()

# ── Thinking step factory ──────────────────────────────────────
_step_counter = 0

def _thinking(
    state: AgentState,
    label: str,
    detail: str,
    emoji: str = "💭",
    tokens: int = 0,
    cost: float = 0.0,
    emit: Callable | None = None,
) -> dict:
    global _step_counter
    _step_counter += 1
    step = {
        "id":      _step_counter,
        "emoji":   emoji,
        "label":   label,
        "detail":  detail,
        "tokens":  tokens,
        "cost_usd":round(cost, 6),
        "ts":      time.time(),
    }
    state["thinking_steps"].append(step)
    state["total_tokens"]  += tokens
    state["total_cost_usd"] = round(state["total_cost_usd"] + cost, 6)
    if emit:
        emit("thinking", step)
    return step


# ── OpenAI call ───────────────────────────────────────────────
def _llm(
    messages: list[dict],
    model_key: str,         # key in pipeline config: "supervisor"|"specialist"|"evaluator"
    agent_name: str,
    session_id: str,
    user_id: str,
    role: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, int, int]:
    """
    Unified LLM call.
    In production with LangChain: replace body with
        llm = ChatOpenAI(model=model_name, temperature=temp)
        response = llm.invoke(messages)
    """
    api_key = config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set — edit .env file:\n  OPENAI_API_KEY=sk-..."
        )
    model_name = _cfg.resolve_model(model_key)
    ag_cfg = _cfg.agent(agent_name)
    t = temperature if temperature is not None else ag_cfg.get("temperature", 0.1)
    tok = max_tokens or ag_cfg.get("max_tokens", config.MAX_TOKENS)

    payload = json.dumps({
        "model": model_name, "messages": messages,
        "max_tokens": tok, "temperature": t,
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
        raise RuntimeError(f"OpenAI {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")

    text  = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    log_api_cost(session_id, user_id, role, agent_name,
                 model_name, pt, ct, calc_cost(model_name, pt, ct), "")
    return text, pt, ct


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except Exception:
        return {"summary": raw, "raw": True}


def _load_prompt(agent_name: str, **kwargs) -> str:
    """Load a prompt template from prompts/*.md and fill placeholders."""
    prompt_file = _cfg.agent(agent_name).get("prompt_file", "")
    path = Path(__file__).parent / prompt_file
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


# ══════════════════════════════════════════════════════════════
# NODE 1: SUPERVISOR
# ══════════════════════════════════════════════════════════════
def node_supervisor(state: AgentState, emit: Callable | None = None) -> AgentState:
    """
    GPT-4o planner. Builds the execution plan.
    """
    agents_list = "\n".join(
        f"  {name}: {_cfg.agent(name).get('description', '')}"
        for name in state["allowed_agents"]
    )
    system = _load_prompt("supervisor", agents_list=agents_list)
    if not system:
        system = "You are the ONDOL supervisor. Route to the correct specialist agent."

    history_ctx = ""
    if state["history"]:
        recent = state["history"][-4:]
        history_ctx = "\n\nRecent conversation:\n" + "\n".join(
            f"  [{m['role']}]: {m['content'][:100]}" for m in recent
        )

    user_msg = f"User question: {state['question']}\nUser role: {state['role']}{history_ctx}"

    _thinking(state, "Supervisor reading question", state["question"],
              emoji="🧠", emit=emit)

    raw, pt, ct = _llm(
        [{"role": "system", "content": system},
         {"role": "user",   "content": user_msg}],
        model_key="supervisor", agent_name="supervisor",
        session_id=state["session_id"], user_id=state["user_id"],
        role=state["role"],
    )
    plan = _parse_json(raw)

    # Validate agent names
    valid = set(state["allowed_agents"])
    for step in plan.get("steps", []):
        if step.get("agent") not in valid:
            step["agent"] = _keyword_fallback(state["question"], list(valid))

    state["plan"] = plan
    cost = calc_cost(_cfg.resolve_model("supervisor"), pt, ct)
    _thinking(
        state,
        f"Plan: {len(plan.get('steps', []))} step(s) · {plan.get('complexity', 'simple')}",
        f"Routing to: {[s['agent'] for s in plan.get('steps', [])]}. "
        f"Reason: {plan.get('reasoning', '')}",
        emoji=_cfg.agent("supervisor").get("thinking_emoji", "🧠"),
        tokens=pt+ct, cost=cost, emit=emit,
    )
    return state


# ══════════════════════════════════════════════════════════════
# NODE 2: SCHEMA DISCOVERY
# ══════════════════════════════════════════════════════════════
def node_schema_discovery(state: AgentState, emit: Callable | None = None) -> AgentState:
    """
    Genie-style: introspect DB, sample values, generate data profile.
    Only runs when a text_to_sql or infra_ops step is in the plan.
    """
    from core.rbac import ROLES
    role_cfg = ROLES.get(state["role"], ROLES["IT Staff"])
    allowed_tables = role_cfg["tables"]

    _thinking(state, "Exploring database schema", "Scanning tables and sampling values...",
              emoji="🔍", emit=emit)

    ctx = get_schema_context(
        config.DB_PATH, allowed_tables, state["question"],
        use_llm_profile=False,   # use prompt-based profile below
    )
    state["schema_text"] = ctx["schema_text"]
    state["snapshot"]    = ctx["snapshot"]

    # LLM-powered data profile using schema_discovery system prompt
    system = _load_prompt("schema_discovery")
    if system and config.OPENAI_API_KEY:
        schema_short = ctx["schema_text"][:2000]
        user_msg = f"Question: {state['question']}\n\nSchema:\n{schema_short}"
        try:
            profile_raw, pt, ct = _llm(
                [{"role": "system", "content": system},
                 {"role": "user",   "content": user_msg}],
                model_key="specialist", agent_name="schema_discovery",
                session_id=state["session_id"], user_id=state["user_id"],
                role=state["role"], max_tokens=300,
            )
            state["data_profile"] = profile_raw
            cost = calc_cost(_cfg.resolve_model("specialist"), pt, ct)
            _thinking(
                state,
                f"Discovered {len(ctx['snapshot'])} tables",
                f"Relevant tables: {ctx['relevant_tables']}\n{profile_raw[:200]}",
                emoji="🔍", tokens=pt+ct, cost=cost, emit=emit,
            )
        except Exception as e:
            state["data_profile"] = ""
            _thinking(state, "Schema profiling skipped", str(e), emoji="⚠", emit=emit)
    else:
        _thinking(
            state,
            f"Discovered {len(ctx['snapshot'])} tables",
            f"Relevant: {ctx['relevant_tables']}. Schema loaded ({len(ctx['schema_text'])} chars).",
            emoji="🔍", emit=emit,
        )
    return state


# ══════════════════════════════════════════════════════════════
# NODE 3: SEMANTIC LAYER (no LLM — pure Python enrichment)
# ══════════════════════════════════════════════════════════════
def node_semantic_layer(state: AgentState, emit: Callable | None = None) -> AgentState:
    """
    Enrich with business glossary, metric registry, join paths.
    No LLM call — deterministic, instant.
    """
    sem = build_context(state["question"], state["role"], config.DB_PATH)
    state["semantic_context"] = sem["full_context"]
    state["kpi_name"]         = sem.get("kpi_name", "") or ""

    detail_parts = []
    if sem["relevant_metrics"]:
        detail_parts.append(f"Metrics: {sem['relevant_metrics']}")
    if sem["glossary_matches"]:
        detail_parts.append(f"Glossary: {sem['glossary_matches']}")
    if sem.get("kpi_name"):
        detail_parts.append(f"KPI shortcut: {sem['kpi_name']}")

    _thinking(
        state,
        "Semantic layer enrichment",
        " | ".join(detail_parts) or "No specific metrics or glossary matches found.",
        emoji="📚", emit=emit,
    )
    return state


# ══════════════════════════════════════════════════════════════
# NODE 4: SPECIALIST AGENTS
# ══════════════════════════════════════════════════════════════

def _exec_sql(sql: str, role: str) -> dict:
    try:
        conn = sqlite3.connect(str(config.DB_PATH))
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"columns": cols, "data": mask_result_pii(data, role),
                "row_count": len(data)}
    except sqlite3.OperationalError as e:
        return {"error": str(e)}

# ── SQL cache ──────────────────────────────────────────────────
_sql_cache: dict[str, tuple[dict, float]] = {}

def _sql_cache_get(sql: str, role: str) -> dict | None:
    if not config.CACHE_TTL:
        return None
    k = hashlib.sha256(f"{sql}|{role}".encode()).hexdigest()
    e = _sql_cache.get(k)
    if e and time.time() < e[1]:
        return {**e[0], "_cached": True}
    return None

def _sql_cache_set(sql: str, role: str, result: dict) -> None:
    if config.CACHE_TTL:
        k = hashlib.sha256(f"{sql}|{role}".encode()).hexdigest()
        _sql_cache[k] = (result, time.time() + config.CACHE_TTL)

# ── Text-to-SQL ────────────────────────────────────────────────
def node_text_to_sql(
    state: AgentState,
    sub_question: str,
    step_id: str,
    force_model_key: str = "specialist",
    emit: Callable | None = None,
) -> dict:
    """
    Full Text-to-SQL pipeline:
    build prompt → generate SQL → validate → execute → retry if error
    """
    from core.rbac import ROLES
    allowed = ROLES.get(state["role"], ROLES["IT Staff"])["tables"]

    # KPI shortcut check (no LLM needed)
    kpi_name = detect_kpi(sub_question)
    if kpi_name and kpi_name in KPIS:
        sql = KPIS[kpi_name]["sql"].strip()
        sql_clean, err = validate_sql(sql, state["role"])
        if not err:
            cached = _sql_cache_get(sql_clean, state["role"])
            if cached:
                _thinking(state, f"KPI cache hit: {kpi_name}",
                          f"Using pre-defined query for '{kpi_name}'",
                          emoji="⚡", emit=emit)
                return {"sql": sql_clean, "query_result": cached,
                        "explanation": KPIS[kpi_name]["description"],
                        "insight": "Pre-defined KPI query.",
                        "kpi_name": kpi_name, "confidence": "high",
                        "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    system = _load_prompt(
        "text_to_sql",
        schema_context  = state["schema_text"],
        semantic_context= state["semantic_context"],
        data_profile    = state["data_profile"],
        allowed_tables  = str(allowed),
        max_rows        = _cfg.raw.get("compliance", {}).get("max_query_rows", 200),
    )
    model_key = force_model_key
    model_name = _cfg.resolve_model(model_key)

    _thinking(state, "SQL Agent: reading schema + semantics",
              f"Question: {sub_question}", emoji="📊", emit=emit)

    raw, pt, ct = _llm(
        [{"role": "system", "content": system},
         {"role": "user",   "content": f'Question: "{sub_question}"'}],
        model_key=model_key, agent_name="text_to_sql",
        session_id=state["session_id"], user_id=state["user_id"],
        role=state["role"],
    )
    result = _parse_json(raw)
    sql_raw = result.get("sql", "")

    _thinking(state, "SQL generated",
              f"```sql\n{sql_raw[:300]}\n```\nTables: {result.get('tables_used', [])}",
              emoji="📊", tokens=pt+ct,
              cost=calc_cost(model_name, pt, ct), emit=emit)

    # Validate
    sql_clean, err = validate_sql(sql_raw, state["role"]) if sql_raw else (None, "No SQL")
    total_pt, total_ct = pt, ct

    # Retry once on error
    if err and sql_raw:
        _thinking(state, "SQL validation failed — retrying",
                  f"Error: {err}", emoji="🔄", emit=emit)
        retry_msg = f"SQL error: {err}\nFailed SQL:\n{sql_raw}\nFix and return corrected JSON."
        r2, pt2, ct2 = _llm(
            [{"role": "system", "content": system},
             {"role": "user",   "content": retry_msg}],
            model_key=model_key, agent_name="text_to_sql_retry",
            session_id=state["session_id"], user_id=state["user_id"], role=state["role"],
        )
        total_pt += pt2; total_ct += ct2
        result2 = _parse_json(r2)
        sql_raw   = result2.get("sql", sql_raw)
        sql_clean2, err2 = validate_sql(sql_raw, state["role"]) if sql_raw else (None, err)
        if not err2:
            sql_clean, err, result = sql_clean2, err2, result2
            _thinking(state, "SQL retry succeeded", f"```sql\n{sql_clean[:200]}\n```",
                      emoji="✔", tokens=pt2+ct2,
                      cost=calc_cost(model_name, pt2, ct2), emit=emit)
        else:
            err = err2

    # Execute
    if err:
        result["query_result"] = {"error": err}
        _thinking(state, "SQL execution failed", err, emoji="❌", emit=emit)
    elif sql_clean:
        cached = _sql_cache_get(sql_clean, state["role"])
        if cached:
            result["query_result"] = cached
            _thinking(state, "Query result (cache hit)",
                      f"{cached.get('row_count', 0)} rows returned (cached)",
                      emoji="⚡", emit=emit)
        else:
            qr = _exec_sql(sql_clean, state["role"])
            result["query_result"] = qr
            if "error" not in qr:
                _sql_cache_set(sql_clean, state["role"], qr)
            _thinking(state, "Query executed",
                      f"{qr.get('row_count', 0)} rows returned. "
                      f"Columns: {qr.get('columns', [])}",
                      emoji="✅" if "error" not in qr else "❌", emit=emit)

    result["sql"]               = sql_clean or sql_raw
    result["prompt_tokens"]     = total_pt
    result["completion_tokens"] = total_ct
    result["cost_usd"]          = calc_cost(model_name, total_pt, total_ct)
    result["model_used"]        = model_name
    return result


# ── Generic specialist runner ──────────────────────────────────
def node_specialist(
    state: AgentState,
    agent_name: str,
    sub_question: str,
    step_id: str,
    force_model_key: str = "specialist",
    emit: Callable | None = None,
) -> dict:
    """Generic runner for arch_review, access_request, infra_ops, security_triage."""
    ag = _cfg.agent(agent_name)
    system = _load_prompt(agent_name)
    model_key = force_model_key
    model_name = _cfg.resolve_model(model_key)

    _thinking(
        state,
        ag.get("thinking_label", agent_name),
        sub_question[:150],
        emoji=ag.get("thinking_emoji", "🤖"), emit=emit,
    )

    raw, pt, ct = _llm(
        [{"role": "system", "content": system},
         {"role": "user",   "content": sub_question}],
        model_key=model_key, agent_name=agent_name,
        session_id=state["session_id"], user_id=state["user_id"],
        role=state["role"],
    )
    result = _parse_json(raw)
    cost = calc_cost(model_name, pt, ct)

    # Show brief output preview in thinking
    preview = result.get("summary") or result.get("classification") or result.get("decision") or ""
    _thinking(
        state,
        f"{ag.get('thinking_label','Agent')} complete",
        preview[:200] if preview else "Output generated.",
        emoji="✅", tokens=pt+ct, cost=cost, emit=emit,
    )

    result.update({
        "prompt_tokens": pt, "completion_tokens": ct,
        "cost_usd": cost, "model_used": model_name,
    })
    return result


# ══════════════════════════════════════════════════════════════
# NODE 5: EVALUATOR
# ══════════════════════════════════════════════════════════════
def node_evaluator(
    state: AgentState,
    agent_name: str,
    agent_output: dict,
    sub_question: str,
    emit: Callable | None = None,
) -> dict:
    """
    GPT-4o-mini quality gate. Scores on 4 dimensions, returns EvalResult dict.
    """
    system = _load_prompt("evaluator")
    if not system:
        system = "Score the agent output on sql_correctness, completeness, hallucination, safety (0-100 each). Return JSON."

    _thinking(state, "Evaluating output quality", "Running quality checks...",
              emoji="🔎", emit=emit)

    output_summary = json.dumps({
        k: v for k, v in agent_output.items()
        if k not in ("query_result",)
    }, default=str)[:1000]

    qr = agent_output.get("query_result", {})
    result_shape = (
        f"SQL returned {qr.get('row_count', 0)} rows, columns: {qr.get('columns', [])}"
        if isinstance(qr, dict) and "data" in qr
        else f"SQL error: {qr.get('error', '')}" if isinstance(qr, dict) and "error" in qr
        else "Non-SQL result"
    )

    user_msg = (
        f"Question: {sub_question}\n"
        f"Agent: {agent_name}\nRole: {state['role']}\n\n"
        f"Output:\n{output_summary}\n\n"
        f"Result shape: {result_shape}\n\n"
        f"Schema (abbreviated):\n{state['schema_text'][:600]}"
    )

    try:
        raw, pt, ct = _llm(
            [{"role": "system", "content": system},
             {"role": "user",   "content": user_msg}],
            model_key="evaluator", agent_name="evaluator",
            session_id=state["session_id"], user_id=state["user_id"],
            role=state["role"], max_tokens=512,
        )
        ev = _parse_json(raw)
    except Exception as e:
        ev = {"sql_correctness": 75, "completeness": 75, "hallucination": 85, "safety": 95,
              "issues": [f"Evaluator error: {e}"], "suggestions": [], "note": "Evaluation failed"}
        pt, ct = 0, 0

    dims = {
        "sql_correctness": ev.get("sql_correctness", 75),
        "completeness":    ev.get("completeness",    75),
        "hallucination":   ev.get("hallucination",   85),
        "safety":          ev.get("safety",          95),
    }
    if agent_name != "text_to_sql":
        dims.pop("sql_correctness", None)

    w_sql = {"sql_correctness": .35, "completeness": .30, "hallucination": .25, "safety": .10}
    w_no  = {"completeness": .50, "hallucination": .35, "safety": .15}
    weights = w_sql if agent_name == "text_to_sql" else w_no
    score = int(sum(dims[k] * weights[k] for k in dims if k in weights))
    threshold = _cfg.raw.get("pipeline", {}).get("eval_pass_threshold", 70)
    passed = score >= threshold
    cost = calc_cost(_cfg.resolve_model("evaluator"), pt, ct)

    _thinking(
        state,
        f"Quality score: {score}/100 {'✅ Pass' if passed else '⚠ Needs retry'}",
        f"Dimensions: {dims}\nIssues: {ev.get('issues', [])[:2]}\n{ev.get('note','')}",
        emoji="✅" if passed else "⚠",
        tokens=pt+ct, cost=cost, emit=emit,
    )

    return {
        "score": score, "passed": passed,
        "needs_retry": not passed,
        "dimensions": dims,
        "issues": ev.get("issues", []),
        "suggestions": ev.get("suggestions", []),
        "note": ev.get("note", ""),
        "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": cost,
    }


# ══════════════════════════════════════════════════════════════
# NODE 6: SUPERVISOR MERGE (multi-step)
# ══════════════════════════════════════════════════════════════
def node_merge(state: AgentState, step_results: list[dict],
               emit: Callable | None = None) -> str:
    """GPT-4o synthesises results from multiple agents into one coherent answer."""
    _thinking(state, "Supervisor merging results",
              f"Combining {len(step_results)} agent outputs...",
              emoji="🔀", emit=emit)

    results_text = "\n\n".join(
        f"--- {r['agent']} ({r['step_id']}) ---\n"
        + json.dumps(r["result"], default=str)[:600]
        for r in step_results
    )
    system = ("You are the ONDOL Supervisor. Combine these specialist agent results "
              "into one coherent, concise answer for the user. Use markdown.")
    user_msg = f"Question: {state['question']}\n\nResults:\n{results_text}"

    try:
        raw, pt, ct = _llm(
            [{"role": "system", "content": system},
             {"role": "user",   "content": user_msg}],
            model_key="supervisor", agent_name="supervisor_merge",
            session_id=state["session_id"], user_id=state["user_id"],
            role=state["role"], max_tokens=800,
        )
        cost = calc_cost(_cfg.resolve_model("supervisor"), pt, ct)
        _thinking(state, "Merge complete", raw[:200],
                  emoji="✅", tokens=pt+ct, cost=cost, emit=emit)
        return raw
    except Exception as e:
        _thinking(state, "Merge failed", str(e), emoji="❌", emit=emit)
        return ""


# ── Keyword fallback ───────────────────────────────────────────
def _keyword_fallback(question: str, allowed: list[str]) -> str:
    q = question.lower()
    for agent, kws in [
        ("arch_review",    ["arb","architecture","review","rfc","standard"]),
        ("access_request", ["access","provision","ad group","sailpoint","permission"]),
        ("infra_ops",      ["vm","infra","cost","runbook","sizing","dr","cloud"]),
        ("security_triage",["alert","security","splunk","soar","threat","malware"]),
    ]:
        if agent in allowed and any(kw in q for kw in kws):
            return agent
    return "text_to_sql" if "text_to_sql" in allowed else allowed[0]
