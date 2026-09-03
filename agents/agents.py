"""
agents/agents.py  —  5 Specialist AI Agents
Each agent is domain-tuned, calls GPT-4o for reasoning,
and logs every token + cost to the cost dashboard.
"""
import os, json, re, sqlite3, urllib.request
from core.rbac import (
    validate_sql, mask_result_pii, content_filter_output,
    log_api_cost, calc_cost, get_role
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB   = os.path.join(BASE, "data", "ondol.db")

# ── Schema context sent to every agent ───────────────────────
SCHEMA = """
Enterprise IT Operations Database — table schemas:

incidents(incident_id,title,team,priority[P1-P4],status[Open/In-Progress/Resolved/Closed],
  created_at,resolved_at,resolution_hours,assignee_id,dept_id,category)

access_requests(req_id,requestor_id,target_system,access_level[Read/Write/Admin],
  status[Pending/Approved/Rejected/Expired],submitted_at,decided_at,sla_hours,dept_id,risk_level[Low/Medium/High])

arb_reviews(arb_id,project_name,submitter_id,status[Draft/Submitted/Approved/Rejected],
  submitted_at,decided_at,prep_hours,technology,dept_id)

infra_assets(asset_id,name,type[VM/Container/Network/Storage/Database],cloud[Azure/AWS/On-Prem],
  region,monthly_cost,cpu_util_pct,mem_util_pct,status[Running/Stopped/Decommissioned],dept_id,last_reviewed)

security_alerts(alert_id,title,severity[P1-P4],status[Open/Investigating/Resolved/False-Positive],
  source[CrowdStrike/Splunk/Defender/Manual],created_at,resolved_at,mttd_minutes,mttr_minutes,category,assignee_id)

departments(dept_id,name,head,region,budget)
employees(emp_id,name,email,dept_id,role,region,hire_date,is_active)
api_cost_log(id,session_id,user_id,user_role,agent,model,prompt_tokens,completion_tokens,cost_usd,query_text,created_at)

Business rules:
- MTTD = mean time to detect (mttd_minutes). MTTR = mean time to resolve (mttr_minutes).
- SLA breach = sla_hours > 48 for access_requests
- Right-size candidate = cpu_util_pct < 20 AND mem_util_pct < 30 AND status='Running'
- P1 = critical, P2 = high, P3 = medium, P4 = low
- All text-to-sql queries must use SQLite syntax (strftime, etc.)
"""


# ── OpenAI API call (GPT-4o) ─────────────────────────────────
def _call_openai(messages: list, model="gpt-4o", max_tokens=1200,
                 session_id="", user_id="", user_role="", agent="") -> tuple[str, int, int]:
    """
    Returns (response_text, prompt_tokens, completion_tokens).
    Falls back gracefully if API key is missing.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Demo mode: return a plausible stub
        stub = _demo_stub(messages[-1]["content"] if messages else "", agent)
        return stub, 0, 0

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    except Exception as e:
        return f"API error: {e}", 0, 0


# ── Demo stubs (no API key needed for showcase) ───────────────
def _demo_stub(question: str, agent: str) -> str:
    q = question.lower()
    if agent == "text_to_sql" or "sql" in q or "query" in q or "show" in q or "how many" in q:
        if "incident" in q or "p1" in q:
            return json.dumps({
                "sql": "SELECT team, priority, COUNT(*) AS cnt, ROUND(AVG(resolution_hours),1) AS avg_hrs\nFROM incidents\nWHERE status IN ('Open','In-Progress')\nGROUP BY team, priority\nORDER BY priority, cnt DESC\nLIMIT 20",
                "explanation": "This query counts open/in-progress incidents grouped by team and priority, showing average resolution hours.",
                "insight": "Teams with high P1/P2 counts and long avg_hrs are likely under-resourced and should be escalated.",
                "confidence": "high"
            })
        if "access" in q or "sla" in q:
            return json.dumps({
                "sql": "SELECT dept_id, risk_level, status,\n  COUNT(*) AS total,\n  ROUND(AVG(sla_hours),1) AS avg_sla_hours\nFROM access_requests\nGROUP BY dept_id, risk_level, status\nORDER BY avg_sla_hours DESC\nLIMIT 15",
                "explanation": "Shows access request volume and average SLA hours by department, risk level, and status.",
                "insight": "Departments with avg_sla_hours > 48 are breaching SLA — prioritise automation for those.",
                "confidence": "high"
            })
        if "cost" in q or "infra" in q or "vm" in q:
            return json.dumps({
                "sql": "SELECT cloud, type,\n  COUNT(*) AS assets,\n  ROUND(SUM(monthly_cost),0) AS total_monthly_usd,\n  ROUND(AVG(cpu_util_pct),1) AS avg_cpu,\n  COUNT(CASE WHEN cpu_util_pct < 20 AND mem_util_pct < 30 THEN 1 END) AS right_size_candidates\nFROM infra_assets\nWHERE status='Running'\nGROUP BY cloud, type\nORDER BY total_monthly_usd DESC",
                "explanation": "Breaks down running infrastructure by cloud and type, highlighting right-sizing candidates (CPU<20%, MEM<30%).",
                "insight": "Right-size candidates represent potential cost savings — check right_size_candidates column.",
                "confidence": "high"
            })
        if "security" in q or "alert" in q or "mttd" in q:
            return json.dumps({
                "sql": "SELECT severity, source,\n  COUNT(*) AS total_alerts,\n  ROUND(AVG(mttd_minutes),1) AS avg_mttd_min,\n  ROUND(AVG(mttr_minutes),1) AS avg_mttr_min,\n  COUNT(CASE WHEN status='Open' THEN 1 END) AS open_alerts\nFROM security_alerts\nGROUP BY severity, source\nORDER BY severity, total_alerts DESC",
                "explanation": "Security alert MTTD/MTTR breakdown by severity and detection source.",
                "insight": "High avg_mttd_min for P1 alerts from a specific source indicates a detection gap that needs tuning.",
                "confidence": "high"
            })
        return json.dumps({
            "sql": "SELECT name, region, budget FROM departments ORDER BY budget DESC",
            "explanation": "Lists all departments ordered by budget.",
            "insight": "Higher-budget departments may have more complex IT needs.",
            "confidence": "medium"
        })

    if agent == "arch_review":
        return json.dumps({
            "summary": "ARB document drafted successfully.",
            "doc": f"## ARB Submission\n\n**Project:** {question[:60]}\n\n### Business Justification\nThis initiative aligns with an enterprise cloud-first strategy and reduces operational overhead by leveraging managed services.\n\n### Technical Architecture\n- **Platform:** Azure (primary)\n- **Pattern:** Event-driven microservices\n- **Security:** Zero Trust, RBAC via Entra ID\n- **Observability:** Azure Monitor + Application Insights\n\n### Risk Assessment\n| Risk | Likelihood | Impact | Mitigation |\n|------|-----------|--------|------------|\n| Vendor lock-in | Medium | Medium | Use open standards (OpenAPI, OTEL) |\n| Data residency | Low | High | Deploy in KR/SG region only |\n\n### Compliance Checklist\n- [x] MAS TRM alignment confirmed\n- [x] Data classification: Internal\n- [x] DR RTO < 4 hours\n- [x] Pen test scheduled Q2\n\n### Next Steps\n1. Submit to ARB portal\n2. Schedule technical review session\n3. Obtain CISO sign-off",
            "ticket": "ARB-DEMO-2024-001",
            "confidence": "high"
        })

    if agent == "access_request":
        risk = "Low" if "read" in q.lower() else "Medium"
        decision = "AUTO-APPROVED" if risk == "Low" else "ESCALATED for manual review"
        return json.dumps({
            "summary": f"Access request processed. Risk: {risk}. Decision: {decision}.",
            "risk_level": risk,
            "decision": decision,
            "ad_groups": ["SYN-Group-ReadOnly"],
            "ticket": "AR-DEMO-2024-001",
            "rationale": f"Risk classified as {risk} based on access level and target system sensitivity. {'Auto-approval applied per policy.' if risk == 'Low' else 'Requires manager approval per IFS-KR policy.'}",
            "confidence": "high"
        })

    if agent == "infra_ops":
        return json.dumps({
            "summary": "Infrastructure analysis complete.",
            "findings": [
                "14 VMs identified with CPU < 20% and MEM < 30% (right-size candidates)",
                "Estimated monthly saving: $3,200 by downsizing to next SKU tier",
                "Azure: 3 underutilised D4s_v3 → D2s_v3 recommended",
                "On-Prem: 2 legacy VMs → Azure migration advised"
            ],
            "runbook": "1. Snapshot VMs\n2. Resize in Azure Portal\n3. Validate service health\n4. Update CMDB",
            "ticket": "INFRA-DEMO-2024-001",
            "confidence": "high"
        })

    if agent == "security_triage":
        return json.dumps({
            "classification": "P2 — High Severity",
            "rationale": "Pattern matches lateral movement indicators: multiple failed authentications followed by successful login from anomalous IP.",
            "playbook": "SOC-PB-007: Lateral Movement Response",
            "splunk_query": 'index=windows_security EventCode=4625 | stats count by src_ip, dest_user | where count > 10 | join dest_user [search index=windows_security EventCode=4624] | table src_ip, dest_user, count, _time',
            "immediate_actions": [
                "Isolate affected endpoint via CrowdStrike RTR",
                "Reset credentials for impacted accounts",
                "Block src_ip at perimeter firewall",
                "Notify SOC lead and open P2 bridge"
            ],
            "ticket": "SEC-DEMO-2024-001",
            "confidence": "high"
        })

    return json.dumps({"summary": "Query processed.", "result": "Demo mode — connect OpenAI API key for live responses.", "confidence": "medium"})


# ── SQL execution ─────────────────────────────────────────────
def execute_sql(sql: str, role: str) -> dict:
    """Execute validated SQL and return results with PII masking."""
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        data = [dict(r) for r in rows]
        conn.close()
        masked = mask_result_pii(data, role)
        return {"columns": cols, "data": masked, "row_count": len(masked)}
    except Exception as e:
        return {"error": str(e)}


# ── Data-to-Text Synthesis ───────────────────────────────────
_SYNTHESIS_SYSTEM = """You are a concise data analyst summarizing query results for IT operations staff.

Rules (follow strictly):
- Write a clear, natural-language answer based ONLY on the provided JSON data.
- Never invent numbers, teams, names, or facts not present in the data.
- If the data array is empty, reply exactly: "조건에 맞는 데이터가 없습니다."
- Keep the answer to 2-4 sentences maximum.
- Highlight the most operationally important insight (e.g. highest count, worst SLA, biggest cost).
- Use Korean when the question is in Korean, English otherwise.
- Do NOT restate the SQL or the column names verbatim.
- Return plain text only — no JSON, no markdown fences, no bullet lists."""

_MAX_SYNTHESIS_ROWS = 50   # safety cap: never send >50 rows to synthesis LLM

def _synthesize_natural_language(
    question: str,
    query_result: dict,
    session_id: str,
    user_id: str,
    role: str,
) -> str:
    """
    Data-to-Text Synthesis node.

    Takes the raw DB result (query_result) and the original user question,
    calls a fast LLM (gpt-4o-mini) to produce a natural-language summary,
    and returns the summary string.

    Token safety:
    - Caps rows at _MAX_SYNTHESIS_ROWS before serialising.
    - Relies on aggregated SQL output from agent_text_to_sql, so the row
      count should already be small in most cases.
    """
    if "error" in query_result:
        return f"데이터 조회 중 오류가 발생했습니다: {query_result['error']}"

    rows = query_result.get("data", [])
    total_rows = query_result.get("row_count", len(rows))

    # Safety cap — truncate if caller somehow passed un-aggregated results
    capped = rows[:_MAX_SYNTHESIS_ROWS]
    truncation_note = (
        f"\n[Note: only first {_MAX_SYNTHESIS_ROWS} of {total_rows} rows shown]"
        if total_rows > _MAX_SYNTHESIS_ROWS else ""
    )

    data_payload = json.dumps(capped, ensure_ascii=False, default=str)
    user_msg = (
        f"User question: {question}\n\n"
        f"Raw data (JSON):{truncation_note}\n{data_payload}"
    )

    messages = [
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user",   "content": user_msg},
    ]
    raw, pt, ct = _call_openai(
        messages,
        model="gpt-4o-mini",
        max_tokens=300,
        session_id=session_id,
        user_id=user_id,
        user_role=role,
        agent="synthesis",
    )
    cost = calc_cost("gpt-4o-mini", pt, ct)
    log_api_cost(session_id, user_id, role, "synthesis", "gpt-4o-mini",
                 pt, ct, cost, question)
    return raw.strip()


def _synthesis_demo_stub(question: str, query_result: dict) -> str:
    """Demo-mode stub for synthesis (no API key)."""
    rows = query_result.get("data", [])
    row_count = query_result.get("row_count", len(rows))
    if not rows:
        return "조건에 맞는 데이터가 없습니다."
    q = question.lower()
    if "p1" in q or "incident" in q:
        teams = {}
        for r in rows:
            t = r.get("team", "Unknown")
            p = r.get("priority", "")
            if p == "P1":
                teams[t] = teams.get(t, 0) + r.get("cnt", 1)
        if teams:
            summary_parts = [f"{t}팀에 {c}건" for t, c in sorted(teams.items(), key=lambda x: -x[1])]
            return f"현재 {'、'.join(summary_parts)}의 P1 인시던트가 열려 있습니다. 총 {row_count}개 팀/우선순위 조합이 조회되었으며, 즉각적인 에스컬레이션이 필요합니다."
    return f"총 {row_count}건의 데이터가 조회되었습니다. 상세 내용은 아래 테이블을 확인하세요."


# ── Base agent runner ─────────────────────────────────────────
def _run_agent(system_prompt: str, user_msg: str, agent_name: str,
               session_id: str, user_id: str, role: str,
               model: str = "gpt-4o") -> tuple[dict, int, int]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_msg}
    ]
    raw, prompt_t, completion_t = _call_openai(
        messages, model=model, session_id=session_id,
        user_id=user_id, user_role=role, agent=agent_name
    )
    cost = calc_cost(model, prompt_t, completion_t)
    log_api_cost(session_id, user_id, role, agent_name, model,
                 prompt_t, completion_t, cost, user_msg)
    # Parse JSON
    try:
        cleaned = re.sub(r'^```json\s*|\s*```$', '', raw.strip())
        result = json.loads(cleaned)
    except Exception:
        result = {"summary": raw, "raw": True}
    return result, prompt_t, completion_t


# ═══════════════════════════════════════════════════════════════
# AGENT 1 — Text-to-SQL (Data Intelligence)
# ═══════════════════════════════════════════════════════════════
def agent_text_to_sql(question: str, session_id: str, user_id: str,
                       role: str, history: list = []) -> dict:
    system = f"""You are ONDOL's Data Intelligence Agent for enterprise IT operations.
Convert natural language questions into precise SQLite SQL queries.

{SCHEMA}

Rules:
- SQLite syntax only (strftime, etc.)
- Always use column aliases (AS) for readability
- ROUND() numeric results
- Handle NULLs with COALESCE/IFNULL
- Return ONLY valid JSON, no markdown fences:
{{
  "sql": "the SQL query",
  "explanation": "what this query does (1-2 sentences, plain English)",
  "insight": "business insight from expected results",
  "confidence": "high|medium|low"
}}"""
    user_msg = f'Question: "{question}"'
    result, pt, ct = _run_agent(system, user_msg, "text_to_sql",
                                 session_id, user_id, role)
    # Execute SQL
    sql_raw = result.get("sql", "")
    sql_clean, err = validate_sql(sql_raw, role) if sql_raw else (None, "No SQL generated")
    if err:
        result["query_result"] = {"error": err}
    elif sql_clean:
        result["query_result"] = execute_sql(sql_clean, role)
    result["sql"] = sql_clean or sql_raw
    result["prompt_tokens"]     = pt
    result["completion_tokens"] = ct
    result["cost_usd"]          = calc_cost("gpt-4o", pt, ct)

    # ── Data-to-Text Synthesis node ───────────────────────────
    # Converts raw DB JSON into a concise natural-language answer.
    # Uses gpt-4o-mini (fast + cheap) with strict anti-hallucination prompt.
    # Falls back to a deterministic stub when no API key is present.
    api_key = os.environ.get("OPENAI_API_KEY", "")
    qr = result.get("query_result", {})
    if api_key:
        natural_summary = _synthesize_natural_language(
            question, qr, session_id, user_id, role
        )
    else:
        natural_summary = _synthesis_demo_stub(question, qr)

    # Hybrid response shape: { summary (NL text) + data (raw rows) }
    # Frontend renders summary above the data table for best UX.
    result["summary"] = natural_summary
    result["data"]    = qr.get("data", [])

    return result


# ═══════════════════════════════════════════════════════════════
# AGENT 2 — Architecture Review (ARB)
# ═══════════════════════════════════════════════════════════════
def agent_arch_review(request: str, session_id: str, user_id: str, role: str) -> dict:
    system = """You are ONDOL's Architecture Review Agent for enterprise IT operations.
You draft ARB (Architecture Review Board) submission documents and check
compliance with enterprise standards.

Return ONLY valid JSON:
{
  "summary": "one-sentence summary",
  "doc": "full ARB document in markdown format with sections: Business Justification, Technical Architecture, Risk Assessment table, Compliance Checklist, Next Steps",
  "ticket": "ARB-YYYY-NNN",
  "confidence": "high|medium|low"
}"""
    result, pt, ct = _run_agent(system, request, "arch_review",
                                 session_id, user_id, role)
    result["prompt_tokens"]     = pt
    result["completion_tokens"] = ct
    result["cost_usd"]          = calc_cost("gpt-4o", pt, ct)
    return result


# ═══════════════════════════════════════════════════════════════
# AGENT 3 — Access Request (AD/IAM)
# ═══════════════════════════════════════════════════════════════
def agent_access_request(request: str, session_id: str, user_id: str, role: str) -> dict:
    system = """You are ONDOL's Access Request Agent for enterprise IT operations.
You process AD group provisioning and SailPoint entitlement requests.

Risk classification:
- Low: Read-only access to non-sensitive systems → auto-approve
- Medium: Write access or sensitive system → require manager approval
- High: Admin access or PII data → require CISO + manager approval

Return ONLY valid JSON:
{
  "summary": "decision summary",
  "risk_level": "Low|Medium|High",
  "decision": "AUTO-APPROVED|ESCALATED for manual review|REJECTED",
  "ad_groups": ["list of AD groups to provision"],
  "ticket": "AR-YYYY-NNN",
  "rationale": "justification for decision",
  "confidence": "high|medium|low"
}"""
    result, pt, ct = _run_agent(system, request, "access_request",
                                 session_id, user_id, role)
    result["prompt_tokens"]     = pt
    result["completion_tokens"] = ct
    result["cost_usd"]          = calc_cost("gpt-4o", pt, ct)
    return result


# ═══════════════════════════════════════════════════════════════
# AGENT 4 — Infra Ops
# ═══════════════════════════════════════════════════════════════
def agent_infra_ops(request: str, session_id: str, user_id: str, role: str) -> dict:
    system = """You are ONDOL's Infrastructure Operations Agent for enterprise IT operations.
You analyse VM right-sizing, cloud costs, and runbook execution.

Return ONLY valid JSON:
{
  "summary": "findings summary",
  "findings": ["bullet point findings"],
  "runbook": "step-by-step runbook if applicable",
  "ticket": "INFRA-YYYY-NNN",
  "confidence": "high|medium|low"
}"""
    result, pt, ct = _run_agent(system, request, "infra_ops",
                                 session_id, user_id, role)
    result["prompt_tokens"]     = pt
    result["completion_tokens"] = ct
    result["cost_usd"]          = calc_cost("gpt-4o", pt, ct)
    return result


# ═══════════════════════════════════════════════════════════════
# AGENT 5 — Security Triage
# ═══════════════════════════════════════════════════════════════
def agent_security_triage(alert: str, session_id: str, user_id: str, role: str) -> dict:
    system = """You are ONDOL's Security Triage Agent for an enterprise IT SOC.
You classify alerts, recommend SOAR playbooks, and generate Splunk queries.

Return ONLY valid JSON:
{
  "classification": "P1|P2|P3|P4 — Severity label",
  "rationale": "why this classification",
  "playbook": "SOC playbook name to trigger",
  "splunk_query": "SPL query for threat hunting",
  "immediate_actions": ["ordered action list"],
  "ticket": "SEC-YYYY-NNN",
  "confidence": "high|medium|low"
}"""
    result, pt, ct = _run_agent(system, alert, "security_triage",
                                 session_id, user_id, role)
    result["prompt_tokens"]     = pt
    result["completion_tokens"] = ct
    result["cost_usd"]          = calc_cost("gpt-4o", pt, ct)
    return result


# ═══════════════════════════════════════════════════════════════
# ROUTER — intent classification (GPT-4o-mini for cost efficiency)
# ═══════════════════════════════════════════════════════════════
def route_intent(question: str, session_id: str, user_id: str, role: str) -> str:
    """
    Classify user intent → agent name.
    Uses gpt-4o-mini (cheaper) for routing.
    """
    system = """Classify this IT request into exactly one of these agents:
text_to_sql     — data queries, KPIs, metrics, counts, trends, SQL questions
arch_review     — ARB documents, architecture review, RFC, tech standards
access_request  — AD groups, access provisioning, SailPoint, permissions
infra_ops       — VM sizing, cloud cost, runbooks, capacity, DR
security_triage — security alerts, SOAR, Splunk, incidents, threat hunting

Reply with ONLY the agent name, nothing else."""

    raw, pt, ct = _call_openai(
        [{"role": "system", "content": system},
         {"role": "user",   "content": question}],
        model="gpt-4o-mini", max_tokens=20,
        session_id=session_id, user_id=user_id, user_role=role, agent="router"
    )
    cost = calc_cost("gpt-4o-mini", pt, ct)
    log_api_cost(session_id, user_id, role, "router", "gpt-4o-mini", pt, ct, cost, question)

    agent = raw.strip().lower().replace("-", "_")
    valid  = {"text_to_sql", "arch_review", "access_request", "infra_ops", "security_triage"}
    # Fallback: keyword matching
    if agent not in valid:
        q = question.lower()
        if any(w in q for w in ["arb","architecture","review","rfc","standard"]):
            agent = "arch_review"
        elif any(w in q for w in ["access","provision","ad group","sailpoint","permission"]):
            agent = "access_request"
        elif any(w in q for w in ["vm","infra","cost","runbook","sizing","dr","cloud"]):
            agent = "infra_ops"
        elif any(w in q for w in ["alert","security","splunk","soar","threat","malware"]):
            agent = "security_triage"
        else:
            agent = "text_to_sql"
    return agent


AGENT_MAP = {
    "text_to_sql":    agent_text_to_sql,
    "arch_review":    agent_arch_review,
    "access_request": agent_access_request,
    "infra_ops":      agent_infra_ops,
    "security_triage":agent_security_triage,
}
