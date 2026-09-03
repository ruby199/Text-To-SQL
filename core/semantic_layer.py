"""
core/semantic_layer.py — ONDOL Semantic Layer

Sits between raw SQL schema and the LLM.
Translates business intent into technical SQL context.

Components:
  METRICS    — named calculations (MTTD, SLA breach rate, etc.)
  GLOSSARY   — business term → column / table mapping
  JOINS      — canonical join paths between tables
  KPIS       — pre-defined named KPI queries
  build_context() — assembles enriched prompt context for any question
"""

from __future__ import annotations
import re, sqlite3
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────
# 1. METRIC REGISTRY
#    name → { sql_expression, description, unit }
# ─────────────────────────────────────────────────────────────
METRICS: dict[str, dict] = {
    "mttd": {
        "description": "Mean Time to Detect — average minutes from event to first detection",
        "sql": "ROUND(AVG(mttd_minutes), 1)",
        "unit": "minutes",
        "table": "security_alerts",
    },
    "mttr": {
        "description": "Mean Time to Resolve — average minutes to resolve a security alert",
        "sql": "ROUND(AVG(mttr_minutes), 1)",
        "unit": "minutes",
        "table": "security_alerts",
        "filter": "status = 'Resolved' AND mttr_minutes IS NOT NULL",
    },
    "sla_breach_rate": {
        "description": "Percentage of access requests that breached the 48-hour SLA",
        "sql": "ROUND(100.0 * SUM(CASE WHEN sla_hours > 48 THEN 1 ELSE 0 END) / COUNT(*), 1)",
        "unit": "percent",
        "table": "access_requests",
    },
    "incident_resolution_rate": {
        "description": "Percentage of incidents in Resolved or Closed status",
        "sql": "ROUND(100.0 * SUM(CASE WHEN status IN ('Resolved','Closed') THEN 1 ELSE 0 END) / COUNT(*), 1)",
        "unit": "percent",
        "table": "incidents",
    },
    "avg_resolution_hours": {
        "description": "Average hours to resolve an incident",
        "sql": "ROUND(AVG(resolution_hours), 1)",
        "unit": "hours",
        "table": "incidents",
        "filter": "resolution_hours IS NOT NULL",
    },
    "avg_arb_prep_hours": {
        "description": "Average hours spent preparing an ARB submission document",
        "sql": "ROUND(AVG(prep_hours), 1)",
        "unit": "hours",
        "table": "arb_reviews",
    },
    "infra_monthly_cost": {
        "description": "Total monthly infrastructure cost across all running assets",
        "sql": "ROUND(SUM(monthly_cost), 0)",
        "unit": "USD",
        "table": "infra_assets",
        "filter": "status = 'Running'",
    },
    "right_size_savings": {
        "description": "Potential monthly savings from right-sizing under-utilised assets (40% saving estimate)",
        "sql": "ROUND(SUM(monthly_cost) * 0.4, 0)",
        "unit": "USD",
        "table": "infra_assets",
        "filter": "cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'",
    },
    "open_p1_count": {
        "description": "Number of currently open P1 (critical) incidents",
        "sql": "COUNT(*)",
        "unit": "count",
        "table": "incidents",
        "filter": "priority = 'P1' AND status IN ('Open','In-Progress')",
    },
    "open_p2_count": {
        "description": "Number of currently open P2 (high severity) incidents",
        "sql": "COUNT(*)",
        "unit": "count",
        "table": "incidents",
        "filter": "priority = 'P2' AND status IN ('Open','In-Progress')",
    },
    "arb_approval_rate": {
        "description": "Percentage of ARB submissions that were approved",
        "sql": "ROUND(100.0 * SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN status IN ('Approved','Rejected') THEN 1 ELSE 0 END), 0), 1)",
        "unit": "percent",
        "table": "arb_reviews",
    },
    "pending_access_count": {
        "description": "Number of access requests currently awaiting decision",
        "sql": "COUNT(*)",
        "unit": "count",
        "table": "access_requests",
        "filter": "status = 'Pending'",
    },
    "false_positive_rate": {
        "description": "Percentage of security alerts identified as false positives",
        "sql": "ROUND(100.0 * SUM(CASE WHEN false_positive = 1 THEN 1 ELSE 0 END) / COUNT(*), 1)",
        "unit": "percent",
        "table": "security_alerts",
    },
}


# ─────────────────────────────────────────────────────────────
# 2. BUSINESS GLOSSARY
#    Maps natural-language terms to SQL column/table/value
# ─────────────────────────────────────────────────────────────
GLOSSARY: dict[str, dict] = {
    # Priority / severity terms
    "critical":       {"maps_to": "priority = 'P1'",  "context": "incidents"},
    "high priority":  {"maps_to": "priority = 'P2'",  "context": "incidents"},
    "p1":             {"maps_to": "priority = 'P1'",  "context": "incidents or alerts"},
    "p2":             {"maps_to": "priority = 'P2'",  "context": "incidents or alerts"},
    "p3":             {"maps_to": "priority = 'P3'",  "context": "incidents or alerts"},
    "p4":             {"maps_to": "priority = 'P4'",  "context": "incidents or alerts"},

    # Incident status
    "open incidents":    {"maps_to": "status IN ('Open','In-Progress')",    "table": "incidents"},
    "unresolved":        {"maps_to": "status IN ('Open','In-Progress')",    "table": "incidents or alerts"},
    "resolved":          {"maps_to": "status IN ('Resolved','Closed')",     "table": "incidents"},
    "closed":            {"maps_to": "status = 'Closed'",                   "table": "incidents"},
    "in-progress":       {"maps_to": "status = 'In-Progress'",              "table": "incidents"},

    # Security alert status
    "investigating":     {"maps_to": "status = 'Investigating'",            "table": "security_alerts"},
    "false positive":    {"maps_to": "status = 'False-Positive'",           "table": "security_alerts"},
    "open alerts":       {"maps_to": "status IN ('Open','Investigating')",  "table": "security_alerts"},

    # Access request terms
    "pending":           {"maps_to": "status = 'Pending'",                  "table": "access_requests"},
    "approved":          {"maps_to": "status = 'Approved'",                 "table": "access_requests or arb_reviews"},
    "rejected":          {"maps_to": "status = 'Rejected'",                 "table": "access_requests or arb_reviews"},
    "expired":           {"maps_to": "status = 'Expired'",                  "table": "access_requests"},

    # Business metrics
    "sla breach":        {"maps_to": "sla_hours > 48",                      "table": "access_requests"},
    "sla breached":      {"maps_to": "sla_hours > 48",                      "table": "access_requests"},
    "overdue access":    {"maps_to": "sla_hours > 48 AND status = 'Pending'","table": "access_requests"},
    "right-size":        {"maps_to": "cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'", "table": "infra_assets"},
    "right-sizing":      {"maps_to": "cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running'", "table": "infra_assets"},
    "underutilised":     {"maps_to": "cpu_util_pct < 20 AND mem_util_pct < 30", "table": "infra_assets"},
    "over-provisioned":  {"maps_to": "cpu_util_pct < 20 AND mem_util_pct < 30", "table": "infra_assets"},
    "low risk":          {"maps_to": "risk_level = 'Low'",                  "table": "access_requests"},
    "medium risk":       {"maps_to": "risk_level = 'Medium'",               "table": "access_requests"},
    "high risk":         {"maps_to": "risk_level = 'High'",                 "table": "access_requests"},
    "jit":               {"maps_to": "jit_access = 1",                      "table": "access_requests"},
    "just-in-time":      {"maps_to": "jit_access = 1",                      "table": "access_requests"},

    # Cloud / infra
    "azure assets":      {"maps_to": "cloud = 'Azure'",                     "table": "infra_assets"},
    "aws assets":        {"maps_to": "cloud = 'AWS'",                       "table": "infra_assets"},
    "on-prem":           {"maps_to": "cloud = 'On-Prem'",                   "table": "infra_assets"},
    "running vms":       {"maps_to": "type = 'VM' AND status = 'Running'",  "table": "infra_assets"},
    "critical assets":   {"maps_to": "criticality = 'Critical'",            "table": "infra_assets"},
    "decommissioned":    {"maps_to": "status = 'Decommissioned'",           "table": "infra_assets"},
    "stopped assets":    {"maps_to": "status = 'Stopped'",                  "table": "infra_assets"},

    # ARB terms
    "draft arb":         {"maps_to": "status = 'Draft'",                    "table": "arb_reviews"},
    "submitted arb":     {"maps_to": "status = 'Submitted'",               "table": "arb_reviews"},
    "confidential":      {"maps_to": "data_classification = 'Confidential'","table": "arb_reviews"},
    "internal":          {"maps_to": "data_classification = 'Internal'",    "table": "arb_reviews"},

    # Change types
    "resize":            {"maps_to": "change_type = 'RESIZE'",              "table": "change_log"},
    "patch":             {"maps_to": "change_type = 'PATCH'",               "table": "change_log"},
    "migration":         {"maps_to": "change_type = 'MIGRATION'",           "table": "change_log"},
    "failed change":     {"maps_to": "outcome = 'Failed'",                  "table": "change_log"},
    "rolled back":       {"maps_to": "outcome = 'Rolled-back'",             "table": "change_log"},

    # Time references
    "this month":  {"maps_to": "strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"},
    "this week":   {"maps_to": "created_at >= date('now', '-7 days')"},
    "last month":  {"maps_to": "strftime('%Y-%m', created_at) = strftime('%Y-%m', date('now','-1 month'))"},
    "last quarter":{"maps_to": "created_at >= date('now', '-90 days')"},
    "today":       {"maps_to": "date(created_at) = date('now')"},
    "this year":   {"maps_to": "strftime('%Y', created_at) = strftime('%Y', 'now')"},
}


# ─────────────────────────────────────────────────────────────
# 3. CANONICAL JOIN PATHS
#    Avoids LLM guessing wrong join conditions
# ─────────────────────────────────────────────────────────────
JOINS: dict[tuple, str] = {
    ("incidents",        "departments"):     "incidents.dept_id = departments.dept_id",
    ("incidents",        "employees"):       "incidents.assignee_id = employees.emp_id",
    ("access_requests",  "departments"):     "access_requests.dept_id = departments.dept_id",
    ("access_requests",  "employees"):       "access_requests.requestor_id = employees.emp_id",
    ("arb_reviews",      "departments"):     "arb_reviews.dept_id = departments.dept_id",
    ("arb_reviews",      "employees"):       "arb_reviews.submitter_id = employees.emp_id",
    ("infra_assets",     "departments"):     "infra_assets.dept_id = departments.dept_id",
    ("infra_assets",     "change_log"):      "infra_assets.asset_id = change_log.asset_id",
    ("security_alerts",  "employees"):       "security_alerts.assignee_id = employees.emp_id",
    ("change_log",       "employees"):       "change_log.changed_by = employees.emp_id",
    ("cost_forecast",    "departments"):     "cost_forecast.dept_id = departments.dept_id",
    ("employees",        "departments"):     "employees.dept_id = departments.dept_id",
}

def get_join(table_a: str, table_b: str) -> str | None:
    """Return the canonical JOIN condition between two tables (order-insensitive)."""
    return JOINS.get((table_a, table_b)) or JOINS.get((table_b, table_a))


# ─────────────────────────────────────────────────────────────
# 4. PRE-DEFINED KPI QUERIES
#    Named queries the LLM can reference or the UI can call directly
# ─────────────────────────────────────────────────────────────
KPIS: dict[str, dict] = {
    "incident_dashboard": {
        "description": "Incident KPIs: open counts, avg resolution hours, SLA by priority",
        "sql": """
SELECT
    priority,
    COUNT(*)                                                        AS total,
    SUM(CASE WHEN status IN ('Open','In-Progress') THEN 1 ELSE 0 END) AS open_count,
    SUM(CASE WHEN status IN ('Resolved','Closed')  THEN 1 ELSE 0 END) AS resolved_count,
    ROUND(AVG(CASE WHEN resolution_hours IS NOT NULL THEN resolution_hours END), 1) AS avg_resolution_hrs,
    ROUND(MIN(CASE WHEN resolution_hours IS NOT NULL THEN resolution_hours END), 1) AS min_hrs,
    ROUND(MAX(CASE WHEN resolution_hours IS NOT NULL THEN resolution_hours END), 1) AS max_hrs
FROM incidents
GROUP BY priority
ORDER BY priority""",
    },
    "incident_by_category": {
        "description": "Incident volume and resolution rate by category",
        "sql": """
SELECT
    category,
    environment,
    COUNT(*)                                                               AS total,
    SUM(CASE WHEN status IN ('Open','In-Progress') THEN 1 ELSE 0 END)    AS open_count,
    ROUND(100.0 * SUM(CASE WHEN status IN ('Resolved','Closed') THEN 1 ELSE 0 END) / COUNT(*), 1) AS resolved_pct,
    ROUND(AVG(CASE WHEN resolution_hours IS NOT NULL THEN resolution_hours END), 1) AS avg_hrs
FROM incidents
GROUP BY category, environment
ORDER BY total DESC""",
    },
    "access_sla_dashboard": {
        "description": "Access request SLA performance by department and risk level",
        "sql": """
SELECT
    dept_id,
    risk_level,
    COUNT(*)                                                            AS total_requests,
    SUM(CASE WHEN sla_hours > 48 THEN 1 ELSE 0 END)                   AS sla_breaches,
    ROUND(AVG(sla_hours), 1)                                            AS avg_sla_hours,
    ROUND(100.0 * SUM(CASE WHEN sla_hours > 48 THEN 1 ELSE 0 END) / COUNT(*), 1) AS breach_rate_pct,
    SUM(CASE WHEN jit_access = 1 THEN 1 ELSE 0 END)                   AS jit_grants,
    ROUND(100.0 * SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) / COUNT(*), 1) AS approval_rate_pct
FROM access_requests
GROUP BY dept_id, risk_level
ORDER BY breach_rate_pct DESC""",
    },
    "security_mttd_dashboard": {
        "description": "Security alert MTTD, MTTR and false positive rate by severity and source",
        "sql": """
SELECT
    severity,
    source,
    COUNT(*)                                                           AS total_alerts,
    SUM(CASE WHEN status IN ('Open','Investigating') THEN 1 ELSE 0 END) AS open_alerts,
    ROUND(AVG(mttd_minutes), 1)                                        AS avg_mttd_min,
    ROUND(AVG(CASE WHEN mttr_minutes IS NOT NULL THEN mttr_minutes END), 1) AS avg_mttr_min,
    SUM(CASE WHEN false_positive = 1 THEN 1 ELSE 0 END)               AS false_positives,
    ROUND(100.0 * SUM(CASE WHEN false_positive = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS fp_rate_pct,
    ROUND(AVG(affected_assets), 1)                                    AS avg_affected_assets
FROM security_alerts
GROUP BY severity, source
ORDER BY severity, total_alerts DESC""",
    },
    "infra_cost_dashboard": {
        "description": "Infrastructure cost, utilisation and right-sizing candidates by cloud and type",
        "sql": """
SELECT
    cloud,
    type,
    COUNT(*)                                                              AS total_assets,
    SUM(CASE WHEN status = 'Running'  THEN 1 ELSE 0 END)                AS running,
    SUM(CASE WHEN status = 'Stopped'  THEN 1 ELSE 0 END)                AS stopped,
    ROUND(SUM(CASE WHEN status = 'Running' THEN monthly_cost ELSE 0 END), 0) AS monthly_cost_usd,
    ROUND(AVG(CASE WHEN status = 'Running' THEN cpu_util_pct END), 1)   AS avg_cpu_pct,
    ROUND(AVG(CASE WHEN status = 'Running' THEN mem_util_pct END), 1)   AS avg_mem_pct,
    COUNT(CASE WHEN cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running' THEN 1 END) AS right_size_candidates,
    ROUND(SUM(CASE WHEN cpu_util_pct < 20 AND mem_util_pct < 30 AND status = 'Running' THEN monthly_cost * 0.4 ELSE 0 END), 0) AS potential_savings_usd
FROM infra_assets
GROUP BY cloud, type
ORDER BY monthly_cost_usd DESC""",
    },
    "arb_pipeline": {
        "description": "ARB review pipeline with approval rates and prep time by technology",
        "sql": """
SELECT
    technology,
    data_classification,
    COUNT(*)                                                                AS total,
    SUM(CASE WHEN status = 'Draft'     THEN 1 ELSE 0 END)                  AS draft,
    SUM(CASE WHEN status = 'Submitted' THEN 1 ELSE 0 END)                  AS submitted,
    SUM(CASE WHEN status = 'Approved'  THEN 1 ELSE 0 END)                  AS approved,
    SUM(CASE WHEN status = 'Rejected'  THEN 1 ELSE 0 END)                  AS rejected,
    ROUND(100.0 * SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN status IN ('Approved','Rejected') THEN 1 ELSE 0 END), 0), 1) AS approval_pct,
    ROUND(AVG(prep_hours), 1)                                               AS avg_prep_hours,
    ROUND(AVG(estimated_cost_usd), 0)                                       AS avg_est_cost_usd
FROM arb_reviews
GROUP BY technology, data_classification
ORDER BY total DESC""",
    },
    "change_success_rate": {
        "description": "Infrastructure change success and rollback rates by change type",
        "sql": """
SELECT
    change_type,
    COUNT(*)                                                               AS total_changes,
    SUM(CASE WHEN outcome = 'Success'     THEN 1 ELSE 0 END)              AS successful,
    SUM(CASE WHEN outcome = 'Failed'      THEN 1 ELSE 0 END)              AS failed,
    SUM(CASE WHEN outcome = 'Rolled-back' THEN 1 ELSE 0 END)              AS rolled_back,
    ROUND(100.0 * SUM(CASE WHEN outcome = 'Success' THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_rate_pct
FROM change_log
GROUP BY change_type
ORDER BY total_changes DESC""",
    },
    "api_cost_summary": {
        "description": "AI API cost breakdown by model and agent",
        "sql": """
SELECT
    model,
    agent,
    COUNT(*)                                       AS calls,
    SUM(prompt_tokens)                             AS total_prompt_tokens,
    SUM(completion_tokens)                         AS total_completion_tokens,
    ROUND(SUM(cost_usd), 6)                        AS total_cost_usd,
    ROUND(AVG(cost_usd), 6)                        AS avg_cost_per_call
FROM api_cost_log
GROUP BY model, agent
ORDER BY total_cost_usd DESC""",
    },
}


# ─────────────────────────────────────────────────────────────
# 5. CONTEXT BUILDER
#    Assembles the enriched prompt context injected into every
#    Text-to-SQL LLM call
# ─────────────────────────────────────────────────────────────

def detect_metrics(question: str) -> list[str]:
    """Return metric names that appear relevant to the question."""
    q = question.lower()
    matches = []
    keywords = {
        "mttd":                   ["mttd", "time to detect", "detection time"],
        "mttr":                   ["mttr", "time to resolve", "resolution time"],
        "sla_breach_rate":        ["sla breach", "sla violation", "breach rate", "overdue"],
        "incident_resolution_rate": ["resolution rate", "resolved incidents", "closure rate"],
        "avg_arb_prep_hours":     ["arb prep", "drafting time", "prep hours"],
        "infra_monthly_cost":     ["infra cost", "monthly cost", "cloud spend", "total cost"],
        "right_size_savings":     ["right-siz", "savings", "underutilis", "overprovisioned"],
        "open_p1_count":          ["p1", "critical incident", "open critical"],
        "arb_approval_rate":      ["arb approval", "approval rate", "approved arb"],
        "pending_access_count":   ["pending access", "access queue", "waiting access"],
    }
    for metric, kws in keywords.items():
        if any(kw in q for kw in kws):
            matches.append(metric)
    return matches


def detect_kpi(question: str) -> str | None:
    """Return a named KPI query if the question clearly maps to one."""
    q = question.lower()
    if any(w in q for w in ["incident dashboard", "incident kpi", "incident overview", "all incident"]):
        return "incident_dashboard"
    if any(w in q for w in ["access sla", "sla dashboard", "access dashboard", "provisioning sla"]):
        return "access_sla_dashboard"
    if any(w in q for w in ["security dashboard", "mttd dashboard", "alert dashboard"]):
        return "security_mttd_dashboard"
    if any(w in q for w in ["infra dashboard", "cost dashboard", "infra overview", "cloud cost"]):
        return "infra_cost_dashboard"
    if any(w in q for w in ["arb pipeline", "arb status", "arb overview"]):
        return "arb_pipeline"
    if any(w in q for w in ["api cost", "model cost", "token cost", "how much did"]):
        return "api_cost_summary"
    return None


def detect_glossary_terms(question: str) -> list[tuple[str, dict]]:
    """Return matching glossary entries for the question."""
    q = question.lower()
    return [(term, info) for term, info in GLOSSARY.items() if term in q]


def build_context(question: str, role: str, db_path: Path) -> dict[str, Any]:
    """
    Main entry point for the semantic layer.
    Returns a dict with:
      - schema_hint     : focused schema snippet relevant to the question
      - metrics_hint    : metric definitions to inject into the prompt
      - glossary_hint   : relevant business-term mappings
      - joins_hint      : canonical join conditions
      - kpi_query       : pre-built SQL if question maps to a named KPI
      - full_context    : assembled string ready to inject into LLM system prompt
    """
    relevant_metrics = detect_metrics(question)
    glossary_matches  = detect_glossary_terms(question)
    kpi_name          = detect_kpi(question)

    # ── Metrics context ────────────────────────────────────────
    metrics_lines = []
    for name in relevant_metrics:
        m = METRICS[name]
        line = f"  • {name}: {m['description']}  [SQL: {m['sql']}]"
        if "filter" in m:
            line += f"  [WHERE: {m['filter']}]"
        metrics_lines.append(line)

    # ── Glossary context ───────────────────────────────────────
    glossary_lines = []
    for term, info in glossary_matches:
        glossary_lines.append(f"  • '{term}' → {info['maps_to']}")

    # ── KPI hint ───────────────────────────────────────────────
    kpi_sql = ""
    kpi_desc = ""
    if kpi_name and kpi_name in KPIS:
        kpi_sql  = KPIS[kpi_name]["sql"].strip()
        kpi_desc = KPIS[kpi_name]["description"]

    # ── Joins context ──────────────────────────────────────────
    joins_lines = [f"  • {t1} ⟷ {t2}: {cond}"
                   for (t1, t2), cond in JOINS.items()]

    # ── Assemble ───────────────────────────────────────────────
    parts = []

    if metrics_lines:
        parts.append("BUSINESS METRICS relevant to this question:\n" + "\n".join(metrics_lines))

    if glossary_lines:
        parts.append("TERM MAPPINGS (use these exact SQL conditions):\n" + "\n".join(glossary_lines))

    parts.append("CANONICAL JOIN CONDITIONS (always use these):\n" + "\n".join(joins_lines))

    if kpi_sql:
        parts.append(
            f"PRE-DEFINED KPI QUERY available for '{kpi_desc}':\n"
            f"You MAY use this as a starting point and modify it:\n```sql\n{kpi_sql}\n```"
        )

    return {
        "relevant_metrics": relevant_metrics,
        "glossary_matches": [t for t, _ in glossary_matches],
        "kpi_name":         kpi_name,
        "kpi_sql":          kpi_sql,
        "full_context":     "\n\n".join(parts),
    }


# ─────────────────────────────────────────────────────────────
# 6. SCHEMA INTROSPECTION (live from DB)
#    Reads actual column names and sample values to keep prompts accurate
# ─────────────────────────────────────────────────────────────

_schema_cache: str | None = None

def get_live_schema(db_path: Path, role: str,
                    allowed_tables: list[str]) -> str:
    """
    Returns a DDL-style schema string for tables the role can access.
    Caches after first read.
    """
    global _schema_cache
    if _schema_cache:
        return _schema_cache

    conn = sqlite3.connect(db_path)
    c    = conn.cursor()
    lines: list[str] = []

    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for (tname,) in c.fetchall():
        if tname.startswith("sqlite_"):
            continue
        if allowed_tables != ["*"] and tname not in allowed_tables:
            continue
        c.execute(f"PRAGMA table_info({tname})")
        cols = c.fetchall()
        c.execute(f"SELECT COUNT(*) FROM {tname}")
        row_count = c.fetchone()[0]
        col_defs = ", ".join(
            f"{col[1]} {col[2]}{'  -- PK' if col[5] else ''}"
            for col in cols
        )
        lines.append(f"{tname} ({row_count:,} rows):  {col_defs}")

    conn.close()
    _schema_cache = "\n".join(lines)
    return _schema_cache
