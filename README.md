# ONDOL — AI-First IT Ways of Working
## AI WOW Competition · Team ONDOL · 온돌

> **온돌** (Korean underfloor heating) — warms every corner of your IT operations with AI.

**[한국어](README.ko.md) | English**

![ONDOL Demo](demo/demo_-gif.gif)

[Watch the full video demo on YouTube](https://youtu.be/qUHExHGmuqs)

---

## Table of Contents
1. [What is ONDOL?](#1-what-is-ondol)
2. [Quick Start](#2-quick-start)
3. [Architecture Deep Dive](#3-architecture-deep-dive)
4. [Agentic AI — The Core Concept](#4-agentic-ai--the-core-concept)
5. [Multi-Agent Pipeline Flow](#5-multi-agent-pipeline-flow)
6. [Each Agent Explained](#6-each-agent-explained)
7. [LangGraph Pattern Implementation](#7-langgraph-pattern-implementation)
8. [YAML Pipeline Configuration](#8-yaml-pipeline-configuration)
9. [Semantic Layer (Genie Benchmark)](#9-semantic-layer-genie-benchmark)
10. [SSE Streaming — Real-Time Thinking](#10-sse-streaming--real-time-thinking)
11. [RBAC + IFS-KR Compliance](#11-rbac--ifs-kr-compliance)
12. [Cost Management](#12-cost-management)
13. [BI Dashboard](#13-bi-dashboard)
14. [Database Schema](#14-database-schema)
15. [API Reference](#15-api-reference)
16. [File Structure](#16-file-structure)
17. [To-Be: Enterprise Integration](#17-to-be-enterprise-integration)

---

## 1. What is ONDOL?

ONDOL is an **enterprise-grade Agentic AI platform** for IT departments. It transforms how IT professionals interact with their operational data — replacing manual queries, ticket-based workflows, and dashboard hunting with a natural language AI assistant that:

- **Understands your IT data** like Databricks Genie — by actually reading the database schema, sampling real values, and learning your business terminology
- **Thinks step-by-step** — every decision is streamed to your screen in real time (Supervisor → Schema Discovery → SQL Agent → Evaluator)
- **Enforces role-based access** at the server level — an Architect cannot accidentally see security alert data
- **Evaluates its own output** — a separate Evaluator agent scores every response 0–100 and triggers a retry with a more capable model if quality is insufficient
- **Shows IT Admins every dollar spent** — per-call cost breakdown with 2025 OpenAI pricing
- **Provides live BI dashboards** — incidents, security MTTD, infra cost, access SLA, KPI trends

### Use cases by role

| Role | Key use cases |
|------|--------------|
| **IT Admin** | Any query + cost monitoring + audit log + all agents |
| **Architect** | ARB document drafting, tech standards checks, data queries |
| **Security Ops** | Alert triage (P1-P4), Splunk SPL generation, access provisioning |
| **Infra Engineer** | VM right-sizing, cost optimisation, runbook generation |
| **Data Analyst** | KPI dashboards, trend analysis, SLA reporting (PII masked) |
| **IT Staff** | Basic incident and ARB status queries |

---

## 2. Quick Start

```bash
# 1. Clone and install dependencies
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt

# 2. Create local configuration (never commit .env)
copy .env.example .env
# Edit .env and set OPENAI_API_KEY plus a unique SECRET_KEY

# 3. Generate the database (10 tables, 4,381 rows)
python data/seed.py

# 4. Run
python app.py
# → http://localhost:5001
```

### GitHub and security notes

- `.env`, SQLite databases, logs, and Python cache files are ignored by Git. Keep API keys and production secrets only in environment variables.
- The included accounts and dataset are synthetic demo data. Change all demo credentials before any non-demo deployment.
- Set `DEBUG=false` and use a strong, randomly generated `SECRET_KEY` outside local development.
- If a real API key was ever placed in a local `.env`, revoke and replace it before publishing the repository.

### Data source direction

`data/seed.py` is a disposable synthetic fixture for the local demo and CI. Its
sample rows and probability weights are deliberately hardcoded so the demo is
repeatable; they are not production business rules. A production deployment
should replace this path with a configured data-source adapter:

- Azure SQL or PostgreSQL for operational workloads, using managed identity or environment-based credentials.
- Databricks SQL Warehouse for analytics workloads, reusing the existing semantic-layer interface.
- Deployment configuration to select the adapter without changing agent or prompt code.

### Demo accounts

| Email | Password | Role | What they can do |
|-------|----------|------|-----------------|
| admin@ondol.demo | admin1 | IT Admin | Everything + cost breakdown + audit |
| arch@ondol.demo | arch1 | Architect | ARB, data queries |
| sec@ondol.demo | sec1 | Security Ops | Alerts, access, security data |
| infra@ondol.demo | infra1 | Infra Engineer | Infra ops, cost data |
| data@ondol.demo | data1 | Data Analyst | All data (PII masked) |
| staff@ondol.demo | staff1 | IT Staff | Basic incidents |

### Try these questions (as IT Admin)
- `"How many P1 incidents are open per team this month?"`
- `"Which Azure VMs are right-sizing candidates and what would we save?"`
- `"Draft an ARB document for migrating to Azure Service Bus"`
- `"Triage this alert: 450 failed logins from 10.0.0.5 in 5 minutes"`
- `"Provision read access to Prod-DB-ReadOnly for E0042"`

---

## 3. Architecture Deep Dive

```
┌─────────────────────────────────────────────────────────────────┐
│  User Interface (Web SPA + EventSource SSE)                     │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────────┐
│  Flask API (app.py)                                              │
│  POST /api/ask          GET /api/ask/stream (SSE)               │
│  GET  /api/bi/*         GET /api/health                         │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Security Gate Layer (core/rbac.py)                             │
│  ① PII filter  ② RBAC  ③ Content filter  ④ Synthetic data       │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│  Pipeline Orchestrator (pipeline/graph.py)                      │
│  LangGraph-pattern StateGraph — passes AgentState through nodes │
└──┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
 [GPT-4o]  [mini]    [no LLM]   [mini×5]   [mini]
 Supervisor Schema   Semantic   Specialist  Evaluator
           Discovery  Layer      Agents
```

### Technology choices

| Layer | Technology | Reason |
|-------|-----------|--------|
| Web framework | Flask | Lightweight, SSE-native |
| Orchestration | LangGraph pattern (stdlib) | Same interface as LangGraph, no external deps |
| AI models | OpenAI GPT-4o + GPT-4o-mini | Cost-optimised split |
| Database | SQLite | Zero-config, perfect for demo; swap to PostgreSQL in prod |
| Config | YAML | Human-readable, version-controlled, hot-reloadable |
| Prompts | Markdown files | Separate from code, editable without deployment |
| Frontend | Vanilla JS + Chart.js | No build step, SSE-native |

---

## 4. Agentic AI — The Core Concept

### What makes an AI "agentic"?

Traditional AI interaction: **User → single LLM call → response**

Agentic AI: **User → orchestrator → multiple specialised agents → self-evaluation → retry loop → response**

The key properties of agentic AI:

#### 1. Autonomy
The AI decides *which* tools and *which* agents to invoke based on the question. The user asks "show me cost data AND draft an ARB" — the Supervisor independently determines this needs two agents and plans accordingly.

#### 2. Planning
Before executing, the Supervisor builds an **explicit execution plan** with steps, dependencies, and complexity assessment. This mirrors how a senior engineer reads a requirements doc before coding.

#### 3. Tool use
Agents call real tools: `execute_sql()`, `validate_sql()`, `cache_get/set()`. In production, these extend to ServiceNow ticket creation, AD group lookup, Splunk API calls.

#### 4. Self-evaluation and retry
The Evaluator agent independently scores every output. If quality is insufficient (score < 70/100), the pipeline automatically upgrades to a more capable model and retries — without user intervention.

#### 5. Memory and state
The `AgentState` TypedDict carries context through every node. Multi-turn conversation history is injected into each agent call, enabling follow-up questions like "now filter by the Security team" after a previous query.

#### 6. Transparency
Every decision is streamed to the user in real time via SSE. The user sees the Supervisor's plan, the Schema Discovery findings, the SQL being generated, and the Evaluator's score — not just a final answer.

### Why multi-agent vs single-agent?

**Single LLM approach:**
- One GPT-4o call handles routing + domain knowledge + SQL generation + quality checking
- Expensive (GPT-4o for every call)
- Conflated responsibilities → lower quality
- No independent quality verification

**Multi-agent approach (ONDOL):**
- GPT-4o-mini handles routing (cheap, fast, accurate for classification)
- Specialist agents are domain-tuned with specific system prompts
- Evaluator provides independent quality gate (not the same model that generated the output)
- GPT-4o used only when necessary (supervisor planning + quality failures)
- **Result: ~85% cost reduction vs single GPT-4o approach**

### Agentic AI vs RAG vs Fine-tuning

| Approach | What it does | ONDOL use |
|----------|-------------|-----------|
| RAG | Retrieves relevant text chunks from a vector store | Used in semantic layer (deterministic) |
| Fine-tuning | Adjusts model weights for a specific domain | Considered for production deployment |
| **Agentic AI** | Orchestrates multiple LLMs + tools + feedback loops | **Core architecture** |

Fine-tuning produces a static model. Agentic AI is **dynamic** — it adapts to new questions, retries failures, and calls different tools based on context. For IT operations where data changes daily and questions are unpredictable, agentic AI is the right choice.

---

## 5. Multi-Agent Pipeline Flow

```
User query
    │
    ▼
┌───────────────────────────────────┐
│  SECURITY GATES (pre-AI)          │
│  ① Strip PII from query           │
│  ② Verify RBAC role + permissions │
│  ③ Content filter (anti-injection)│
│  ④ Log to conversation_log        │
└─────────────────┬─────────────────┘
                  │
                  ▼
┌───────────────────────────────────┐  Model: GPT-4o
│  NODE 1: SUPERVISOR               │  Cost:  ~$0.002–0.008
│                                   │
│  • Reads question + role context  │
│  • Builds execution plan (JSON)   │
│  • Detects single vs multi-step   │
│  • Validates agent availability   │
│  • Streams: "🧠 Plan: N steps"   │
└─────────────────┬─────────────────┘
                  │ plan.steps[]
                  ▼
┌───────────────────────────────────┐  Model: GPT-4o-mini
│  NODE 2: SCHEMA DISCOVERY         │  Cost:  ~$0.0002
│  (only if data agent in plan)     │
│                                   │
│  • Reads actual table DDL         │
│  • Samples real column values     │
│  • Notes enum values (P1/P2/P3)   │
│  • Writes focused data profile    │
│  • Streams: "🔍 Discovered N tbl" │
└─────────────────┬─────────────────┘
                  │ schema_text + data_profile
                  ▼
┌───────────────────────────────────┐  No LLM — deterministic
│  NODE 3: SEMANTIC LAYER           │  Cost:  $0.000
│                                   │
│  • Matches glossary terms         │
│  • Injects metric definitions     │
│  • Provides canonical JOIN paths  │
│  • Detects named KPI shortcuts    │
│  • Streams: "📚 Metrics: [...]"  │
└─────────────────┬─────────────────┘
                  │ semantic_context
                  ▼
┌───────────────────────────────────┐  Model: GPT-4o-mini
│  NODE 4: SPECIALIST AGENT         │  Cost:  ~$0.001–0.003
│                                   │
│  One of:                          │
│  • text_to_sql   (SQL + execute)  │
│  • arch_review   (ARB document)   │
│  • access_request(risk + AD)      │
│  • infra_ops     (right-size)     │
│  • security_triage(P1-P4+SPL)     │
│                                   │
│  Streams: "📊 SQL generated"      │
│           "✅ Query: N rows"      │
└─────────────────┬─────────────────┘
                  │ agent_output
                  ▼
┌───────────────────────────────────┐  Model: GPT-4o-mini
│  NODE 5: EVALUATOR                │  Cost:  ~$0.0003
│                                   │
│  Scores independently:            │
│  • sql_correctness  (35%)         │
│  • completeness     (30%)         │
│  • hallucination    (25%)         │
│  • safety           (10%)         │
│                                   │
│  score >= 70 → PASS               │
│  score <  70 → RETRY              │
│                                   │
│  Streams: "✅ Score: 87/100"      │
└────────┬──────────────────────────┘
         │
    ┌────┴─────┐
    │          │
  PASS       RETRY (max 1×)
    │          │
    │    ┌─────▼────────────────────┐  Model: GPT-4o (upgrade)
    │    │  NODE 4b: RETRY          │  Cost:  ~$0.003–0.010
    │    │  Same agent, better model│
    │    │  + evaluator feedback    │
    │    └─────────────────────────┘
    │
    ▼
┌───────────────────────────────────┐  No LLM — deterministic
│  OUTPUT GATE (post-AI)            │
│                                   │
│  ⑤ Output content filter          │
│  ⑥ PII mask (role-based)          │
│  ⑦ Audit log (IFS-KR 1-year)      │
│  ⑧ API cost log                   │
│  ⑨ Cache write (SQL results)      │
└─────────────────┬─────────────────┘
                  │
                  ▼
           User response
     (result + eval score + trace
      + cost card for IT Admin)
```

---

## 6. Each Agent Explained

### Supervisor (GPT-4o)
**File:** `agents/supervisor.py` + `pipeline/prompts/supervisor.md`

The orchestrator. Uses GPT-4o because planning quality directly determines the entire pipeline's accuracy. A wrong routing decision wastes every downstream LLM call.

**What it does:**
1. Reads the question and conversation history
2. Filters available agents by RBAC role
3. Decides single-step vs multi-step execution
4. Generates a structured JSON execution plan with step IDs, agent names, sub-questions, and dependency graph
5. After multi-step execution: merges results into one coherent answer

**Why GPT-4o here:**
Complex multi-step detection requires understanding nuanced natural language. Example: "Show cost data then draft an ARB for the most expensive category" — the Supervisor must recognise this is two sequential steps where step 2 depends on step 1's output. GPT-4o-mini frequently misses this.

---

### Schema Discovery (GPT-4o-mini)
**File:** `agents/schema_discovery.py` + `pipeline/prompts/schema_discovery.md`

Inspired by Databricks Genie's "understand your data" phase.

**What it does:**
1. Connects directly to SQLite and reads `PRAGMA table_info()`
2. Finds enum-like columns (priority, status, severity) and reads their distinct values
3. Samples 3 random rows per table
4. Passes this raw schema to GPT-4o-mini to write a focused 3-5 sentence data profile
5. Caches the profile for 1 hour (schema doesn't change mid-session)

**Why this matters:**
Without schema discovery, the SQL agent might generate `WHERE status = 'InProgress'` when the actual value is `'In-Progress'`. One character difference = no results. Schema discovery eliminates this class of error entirely.

---

### Semantic Layer (No LLM)
**File:** `core/semantic_layer.py`

A deterministic business knowledge layer. No LLM calls — pure Python matching.

**Components:**

*Metric Registry* — Maps business terms to SQL expressions:
```python
"mttd": {
    "sql": "ROUND(AVG(mttd_minutes), 1)",
    "filter": None,
    "table": "security_alerts"
}
"right_size_savings": {
    "sql": "ROUND(SUM(monthly_cost) * 0.4, 0)",
    "filter": "cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'"
}
```

*Glossary* — Maps business terminology to exact SQL conditions:
```python
"right-size":   "cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'"
"sla breach":   "sla_hours > 48"
"this month":   "strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
```

*Join paths* — Canonical join conditions (no LLM guessing):
```python
("incidents", "departments"): "incidents.dept_id = departments.dept_id"
("security_alerts", "employees"): "security_alerts.assignee_id = employees.emp_id"
```

*Named KPI shortcuts* — Pre-built queries bypassing LLM entirely:
```python
"infra_cost_dashboard": {
    "sql": "SELECT cloud, type, COUNT(*), ROUND(SUM(monthly_cost),0) ..."
}
```

---

### Text-to-SQL Agent (GPT-4o-mini)
**File:** `agents/agents.py::agent_text_to_sql` + `pipeline/prompts/text_to_sql.md`

The most complex specialist. Uses everything the preceding nodes built.

**Pipeline:**
1. Check for named KPI shortcut → skip LLM entirely if found
2. Receive schema_text + data_profile from Schema Discovery
3. Receive semantic_context (metrics, glossary, joins) from Semantic Layer
4. Generate SQL with GPT-4o-mini using the enriched prompt
5. Rule-based validation: SELECT only, no DML, RBAC table check
6. Execute against SQLite, apply PII masking
7. If SQL error: retry once with error feedback (same model, same call budget)
8. Write result to 5-minute cache
9. Return: SQL + explanation + insight + query_result + confidence

---

### Evaluator (GPT-4o-mini)
**File:** `agents/evaluator.py` + `pipeline/prompts/evaluator.md`

The quality gate. Crucially, it runs on a **different model instance** from the specialist — so it cannot be biased toward its own output.

**Scoring:**
```
Aggregate score = weighted average of:
  sql_correctness  × 0.35  (does SQL answer the question?)
  completeness     × 0.30  (is everything requested present?)
  hallucination    × 0.25  (any invented columns/tables/stats?)
  safety           × 0.10  (PII exposure, policy violations?)

For non-SQL agents: sql_correctness weight redistributed to completeness.
```

**Decision logic:**
- Score ≥ 70: pass → return to user
- Score < 70: trigger retry with GPT-4o + evaluator feedback injection
- After retry: pass regardless (max 1 retry to control cost)

**Why independent evaluation matters:**
An LLM asked to evaluate its own output will almost always rate it highly (confirmation bias). Using a separate evaluation call catches errors the generating LLM missed: wrong column aliases, missing JOIN conditions, hallucinated table names.

---

## 7. LangGraph Pattern Implementation

ONDOL implements the **exact same patterns** as LangGraph without the library dependency (network is blocked in this environment). Migrating to LangGraph requires only replacing the runner:

### Current (stdlib):
```python
# pipeline/graph.py
state = make_initial_state(...)
state = node_supervisor(state, emit=emit)
state = node_schema_discovery(state, emit=emit)
state = node_semantic_layer(state, emit=emit)
# ... execute steps + evaluator + conditional retry
```

### With LangGraph (production):
```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

graph = StateGraph(AgentState)
graph.add_node("supervisor",        node_supervisor)
graph.add_node("schema_discovery",  node_schema_discovery)
graph.add_node("semantic_layer",    node_semantic_layer)
graph.add_node("text_to_sql",       node_text_to_sql)
graph.add_node("evaluator",         node_evaluator)

# Conditional edges (same logic as our _route_after_eval)
graph.add_conditional_edges(
    "evaluator",
    lambda s: "retry" if s["eval_score"] < 70 and s["retry_count"] < 1 else "output",
    {"retry": "text_to_sql", "output": END}
)

# Human-in-the-loop for P1 security alerts
graph.add_interrupt_before("security_triage_execute")

checkpointer = SqliteSaver.from_conn_string("ondol.db")
compiled = graph.compile(checkpointer=checkpointer)
```

**LangGraph adds:**
- Persistent state checkpointing per thread_id (session)
- `graph.invoke()` / `graph.stream()` replacing our manual runner
- `interrupt_before` for human approval of P1 actions
- Visual graph rendering: `graph.get_graph().draw_mermaid()`
- Production-scale concurrency (parallel step execution)

### State definition (identical for both):
```python
# pipeline/state.py
class AgentState(TypedDict):
    question:         str
    role:             str
    plan:             dict       # supervisor output
    schema_text:      str        # schema discovery output
    semantic_context: str        # semantic layer output
    agent_results:    list[dict] # specialist outputs
    eval_score:       int        # evaluator output
    thinking_steps:   list[dict] # SSE streaming
    total_cost_usd:   float
    # ... 20+ fields
```

The state flows through every node unchanged in shape — each node reads what it needs and writes its outputs.

---

## 8. YAML Pipeline Configuration

**File:** `pipeline/config.yaml`

Everything about the pipeline is configurable without touching Python code:

```yaml
pipeline:
  eval_pass_threshold: 70    # raise to 80 for stricter quality
  max_retries: 1             # set to 2 for more retries

models:
  supervisor:    gpt-4o       # use gpt-4.1 for better planning
  specialist:    gpt-4o-mini  # use gpt-4.1-mini for cheaper calls
  retry_upgrade: gpt-4o       # model to use on evaluator retry

agents:
  text_to_sql:
    max_tokens: 1200
    temperature: 0.1
    prompt_file: prompts/text_to_sql.md   # edit without redeploying
    result_cache_ttl: 300
```

**Hot reload:** `PipelineConfig.reload()` re-reads the YAML without server restart.

**Prompt files** (`pipeline/prompts/*.md`) are Markdown templates with `{{variable}}` placeholders that are filled at runtime. This means:
- Prompts are version-controlled separately from code
- Domain experts can edit prompts without Python knowledge
- A/B testing different prompt strategies is a file edit

---

## 9. Semantic Layer (Genie Benchmark)

ONDOL's semantic layer is benchmarked against Databricks Genie's AI/BI approach:

| Feature | Databricks Genie | ONDOL |
|---------|-----------------|-------|
| Live schema reading | ✅ | ✅ |
| Sample value injection | ✅ | ✅ |
| Business glossary | ✅ | ✅ |
| Metric registry | ✅ | ✅ |
| Named KPI shortcuts | ✅ | ✅ |
| Join path resolution | ✅ | ✅ |
| Multi-turn context | ✅ | ✅ |
| LLM-powered profiling | ✅ | ✅ (GPT-4o-mini) |
| Self-evaluation | ❌ | ✅ (Evaluator agent) |
| RBAC at semantic layer | ❌ | ✅ |

**Why semantic layers matter for SQL generation:**

Without: *"show right-size candidates"* → LLM might generate `WHERE utilisation < 0.2` (wrong column, wrong format, wrong threshold)

With semantic layer: LLM receives the exact condition: `cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'` and uses it verbatim.

---

## 10. SSE Streaming — Real-Time Thinking

**Endpoint:** `GET /api/ask/stream?q=<question>`

The frontend connects via `EventSource` (native browser API). The server streams newline-delimited JSON events as each pipeline node completes:

```
data: {"type": "pipeline_start", "data": {"allowed_agents": [...]}}

data: {"type": "thinking", "data": {
  "emoji": "🧠",
  "label": "Supervisor planning",
  "detail": "Plan: 1 step, complexity=moderate. Routing to text_to_sql.",
  "tokens": 520,
  "cost_usd": 0.002600
}}

data: {"type": "thinking", "data": {
  "emoji": "🔍",
  "label": "Exploring database schema",
  "detail": "Discovered 10 tables. Relevant: [incidents, departments].\nThe term 'P1' maps to priority='P1' in the incidents table.",
  "tokens": 180,
  "cost_usd": 0.000027
}}

data: {"type": "thinking", "data": {
  "emoji": "📊",
  "label": "SQL generated",
  "detail": "```sql\nSELECT team, COUNT(*) AS cnt...\n```",
  "tokens": 350,
  "cost_usd": 0.000053
}}

data: {"type": "thinking", "data": {
  "emoji": "✅",
  "label": "Quality score: 91/100 ✅ Pass",
  "detail": "Dimensions: {sql_correctness: 95, completeness: 90, hallucination: 88, safety: 100}"
}}

data: {"type": "result", "data": { ... full response ... }}
data: {"type": "done"}
```

The `emit` callback is injected into every node function. In a LangGraph deployment, this maps to `graph.stream()` which natively yields state deltas.

---

## 11. RBAC + IFS-KR Compliance

### Role permissions matrix

| Agent | IT Admin | Architect | Security Ops | Infra Eng | Data Analyst | IT Staff |
|-------|:---:|:---:|:---:|:---:|:---:|:---:|
| text_to_sql | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| arch_review | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| access_request | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| infra_ops | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| security_triage | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |

RBAC is enforced at the **pipeline level** — the Supervisor checks `allowed_agents_for_role()` from the YAML config. Even if an agent is requested directly via API, the pipeline blocks it before any LLM call.

### IFS-KR 8 Control Gates

| Gate | Layer | Implementation |
|------|-------|---------------|
| ① PII filter | Pre-AI | `strip_pii()` — regex removes emails, phone numbers, KR IDs |
| ② RBAC | Pre-AI | `allowed_agents_for_role()` from YAML |
| ③ Content filter | Pre-AI | `content_filter_input()` — blocks prompt injection |
| ④ Synthetic data | At-rest | All DB records use `SYN_` prefix |
| ⑤ Output filter | Post-AI | `content_filter_output()` — strips escaped PII |
| ⑥ PII mask | Post-AI | `mask_result_pii()` — masks by role |
| ⑦ Audit log | Always | Every action to `audit_log` table |
| ⑧ Conversation log | Always | 1-year retention, PII-stripped |

---

## 12. Cost Management

### 2025 OpenAI pricing (verified May 2026)

| Model | Input per 1M tokens | Output per 1M tokens | ONDOL use |
|-------|--------------------|--------------------|-----------|
| GPT-4o | $2.50 | $10.00 | Supervisor + retry |
| GPT-4o-mini | $0.15 | $0.60 | Router, specialist agents, evaluator |
| GPT-4.1 | $2.00 | $8.00 | Optional upgrade |
| GPT-4.1-mini | $0.40 | $1.60 | Optional upgrade |

### Cost per query (typical)

| Component | Model | ~Tokens | ~Cost USD |
|-----------|-------|---------|-----------|
| Supervisor | GPT-4o | 600 | $0.0027 |
| Schema Discovery | GPT-4o-mini | 250 | $0.000038 |
| SQL Agent | GPT-4o-mini | 800 | $0.000120 |
| Evaluator | GPT-4o-mini | 400 | $0.000060 |
| **Total (typical)** | | **~2,050** | **~$0.003** |
| Retry path (+) | GPT-4o | +800 | +$0.0060 |

vs. naive single GPT-4o call: ~1,500 tokens = ~$0.018 → **83% cost reduction**

### IT Admin cost card

After every query, IT Admins see:
```
💰 Cost Breakdown (IT Admin)
  🧠 Supervisor planning    ████████████████░░░░  $0.002600
  🔍 Schema Discovery       █░░░░░░░░░░░░░░░░░░░  $0.000027
  📊 SQL Agent              ██░░░░░░░░░░░░░░░░░░  $0.000120
  ✅ Evaluator              █░░░░░░░░░░░░░░░░░░░  $0.000060

  2,050 total tokens  |  1.8s  |  GPT-4o: $2.50/$10.00 /1M  |  mini: $0.15/$0.60 /1M
  Total: $0.00281
```

---

## 13. BI Dashboard

Built-in Chart.js dashboard (accessible to all roles via the "BI" tab):

| Chart | Data source | Insight |
|-------|------------|---------|
| Incidents by priority (12mo) | `incidents` | P1/P2 trend detection |
| Security MTTD by severity/source | `security_alerts` | Detection gap identification |
| Infra cost by cloud & type | `infra_assets` | Cost concentration analysis |
| VM utilisation distribution | `infra_assets` | Right-sizing opportunity |
| KPI weekly trend (2yr) | `kpi_snapshots` | Long-term operational health |
| MITRE ATT&CK top 10 | `security_alerts` | Threat landscape |
| AI API cost analytics | `api_cost_log` | Model spend tracking (Admin only) |

**MCP integration:** The BI endpoints (`/api/bi/*`) are designed to be consumed by external BI tools via MCP. When a Metabase, Grafana, or Tableau MCP connector is available, these endpoints can be registered as data sources, allowing drag-and-drop dashboard building on top of ONDOL's data layer.

---

## 14. Database Schema

10 tables, 4,381+ rows of synthetic IT data:

```
incidents        (1,000 rows) — P1-P4 incidents with resolution times, categories, environments
access_requests  (  600 rows) — AD/SailPoint requests with SLA, JIT flags, risk classification
arb_reviews      (  250 rows) — ARB submissions with tech stack, cost estimates, approval chains
infra_assets     (  400 rows) — VMs/containers with CPU/MEM utilisation, SKU, monthly cost
security_alerts  (  800 rows) — Alerts with MITRE ATT&CK mapping, MTTD/MTTR, IOC counts
employees        (  150 rows) — Synthetic staff across 8 departments, 6 regions
departments      (    8 rows) — IT org units with budgets
change_log       (  300 rows) — Infra change audit trail (resize, patch, deploy)
cost_forecast    (  768 rows) — 24-month cost actuals vs forecast by dept + resource type
kpi_snapshots    (  105 rows) — Weekly KPI history for trend analysis
```

New columns vs v1: `root_cause`, `environment` (incidents); `jit_access`, `justification` (access_requests); `data_classification`, `estimated_cost_usd` (arb_reviews); `sku`, `criticality`, `patched_date` (infra_assets); `mitre_technique`, `false_positive`, `affected_assets` (security_alerts).

---

## 15. API Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | /api/login | None | Authenticate, get session |
| POST | /api/logout | Session | End session |
| GET | /api/health | None | API key status check |
| POST | /api/ask | Session | Synchronous pipeline execution |
| GET | /api/ask/stream | Session | SSE streaming pipeline |
| GET | /api/bi/overview | Session | KPI summary cards |
| GET | /api/bi/incidents | Session | Incident charts data |
| GET | /api/bi/security | Session | Security MTTD + MITRE data |
| GET | /api/bi/infra | Session | Infra cost + utilisation |
| GET | /api/bi/access | Session | Access SLA data |
| GET | /api/bi/kpi_trend | Session | Weekly KPI history |
| GET | /api/bi/costs | IT Admin | AI API cost analytics |
| GET | /api/stats | Session | Live sidebar KPIs |
| GET | /api/schema | Session | RBAC-filtered schema browser |
| GET | /api/audit | IT Admin | Audit + conversation logs |
| GET | /api/samples | Session | Role-aware sample questions |

---

## 16. File Structure

```
ondol/
├── .env                         # API keys (never commit)
├── .env.example                 # Template
├── config.py                    # Single source of truth for all settings
├── app.py                       # Flask server + all API routes + BI endpoints
├── requirements.txt             # flask, pyyaml
│
├── pipeline/                    # LangGraph-pattern orchestration
│   ├── config.yaml              # Master pipeline config (models, agents, RBAC, compliance)
│   ├── state.py                 # AgentState TypedDict (LangGraph-compatible)
│   ├── graph.py                 # StateGraph runner + SSE stream generator
│   ├── nodes.py                 # All node functions (supervisor, schema, semantic, eval)
│   ├── loader.py                # YAML config loader + model resolution
│   └── prompts/
│       ├── supervisor.md        # GPT-4o system prompt
│       ├── text_to_sql.md       # {{schema_context}} {{semantic_context}} variables
│       ├── arch_review.md
│       ├── access_request.md
│       ├── infra_ops.md
│       ├── security_triage.md
│       ├── evaluator.md
│       └── schema_discovery.md
│
├── agents/
│   ├── agents.py                # 5 specialist agents (GPT-4o-mini)
│   ├── supervisor.py            # GPT-4o planner + result merger
│   ├── schema_discovery.py      # Genie-style DB introspection + profiling
│   ├── evaluator.py             # Quality scoring + retry trigger
│   └── pipeline.py              # Legacy (superseded by pipeline/graph.py)
│
├── core/
│   ├── rbac.py                  # RBAC + IFS-KR compliance + cost calc (2025 rates)
│   └── semantic_layer.py        # Metric registry, glossary, join paths, KPI shortcuts
│
├── data/
│   ├── seed.py                  # Synthetic dataset generator (10 tables, 4,381 rows)
│   └── ondol.db                 # SQLite DB (auto-created)
│
└── templates/
    └── index.html               # Full SPA: SSE thinking, BI charts, cost card, RBAC demo
```

---

## 17. To-Be: Enterprise Integration

```
Phase 0 (Now)           Phase 1 (6-8 wks)      Phase 2 (Q2 2025)       Phase 3 (Q3 2025)
─────────────           ─────────────────       ─────────────────       ─────────────────
Direct OpenAI API  →    API gateway             →    AI Foundry          →    Agent marketplace
Flask standalone        FinOps                  MS Agent platform       Publish as template
SQLite audit log        Enterprise logging       LangGraph production     Team ONDOL agents
Built-in BI             Grafana/Metabase MCP     Human-in-the-loop P1    Enterprise RBAC

LangGraph (stdlib)  →   LangGraph + SqliteSaver → LangGraph Cloud    →  Full agent registry
Pipeline YAML           Git-ops pipeline YAML    Blue/green agents       Per-team configs
```

### Migration to LangGraph (production checklist)

- [ ] `pip install langgraph langchain-openai langchain-core`
- [ ] Replace `pipeline/graph.py` runner with `StateGraph.compile()`
- [ ] Add `SqliteSaver` or `PostgresSaver` checkpointer for session persistence
- [ ] Add `interrupt_before("security_triage")` for P1 human approval
- [ ] Replace SQLite with PostgreSQL for multi-instance deployment
- [ ] Add LangSmith tracing for production observability
- [ ] Register agents in an enterprise agent marketplace
- [ ] Connect Grafana/Metabase via MCP for external BI

---

*Built by Team ONDOL for an AI WOW competition. All database records are synthetic.*
