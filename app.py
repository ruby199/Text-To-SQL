"""
app.py  —  ONDOL Flask Application
Routes: /  /api/ask  /api/cost  /api/audit  /api/schema  /api/login  /api/logout
"""
import os, sys, json, uuid, sqlite3
from flask import Flask, request, jsonify, session, render_template_string

sys.path.insert(0, os.path.dirname(__file__))

import config
from core.rbac import (
    authenticate, get_role, ROLES, DEMO_USERS,
    content_filter_input, content_filter_output,
    log_conversation, log_audit, DB
)
from agents.agents import route_intent, AGENT_MAP

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

BASE = os.path.dirname(__file__)


def _db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Auth ──────────────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def login():
    d    = request.json or {}
    user = authenticate(d.get("email", ""), d.get("password", ""))
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    session["user"]       = user
    session["session_id"] = str(uuid.uuid4())
    session["turn"]       = 0
    role_info = get_role(user["role"])
    log_audit(session["session_id"], user["id"], user["role"],
              "LOGIN", f"User {user['name']} logged in", request.remote_addr)
    return jsonify({
        "user": user,
        "role_info": {
            "level":       role_info["level"],
            "description": role_info["description"],
            "pii_visible": role_info["pii_visible"],
            "can_manage":  role_info["can_manage"],
            "can_view_costs": role_info["can_view_costs"],
        },
        "session_id": session["session_id"]
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    if "user" in session:
        log_audit(session.get("session_id",""), session["user"]["id"],
                  session["user"]["role"], "LOGOUT", "Session ended")
    session.clear()
    return jsonify({"ok": True})


# ── Main ask endpoint ─────────────────────────────────────────
@app.route("/api/ask", methods=["POST"])
@login_required
def ask():
    d        = request.json or {}
    question = d.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question required"}), 400

    user       = session["user"]
    session_id = session["session_id"]
    role       = user["role"]
    session["turn"] = session.get("turn", 0) + 1
    turn = session["turn"]

    # ── Gate 1: Input content filter ──────────────────────────
    safe, reason = content_filter_input(question)
    if not safe:
        log_audit(session_id, user["id"], role, "BLOCKED", reason)
        return jsonify({"error": reason, "blocked": True}), 400

    # ── Gate 2: Log conversation (IFS-KR — strip PII) ─────────
    log_conversation(session_id, user["id"], role, turn, "user", question, "router")

    # ── Gate 3: Route to agent (gpt-4o-mini) ──────────────────
    agent_name = route_intent(question, session_id, user["id"], role)

    # ── Gate 4: RBAC — agent availability check ───────────────
    AGENT_ROLE_GATE = {
        "text_to_sql":     ["IT Admin","Architect","Security Ops","Infra Engineer","Data Analyst","IT Staff"],
        "arch_review":     ["IT Admin","Architect"],
        "access_request":  ["IT Admin","Security Ops"],
        "infra_ops":       ["IT Admin","Infra Engineer"],
        "security_triage": ["IT Admin","Security Ops"],
    }
    if role not in AGENT_ROLE_GATE.get(agent_name, []):
        msg = f"Your role ('{role}') does not have access to the {agent_name.replace('_',' ').title()} agent."
        log_audit(session_id, user["id"], role, "ACCESS_DENIED", msg)
        return jsonify({"error": msg, "agent": agent_name}), 403

    # ── Run agent ─────────────────────────────────────────────
    agent_fn = AGENT_MAP[agent_name]
    try:
        if agent_name == "text_to_sql":
            result = agent_fn(question, session_id, user["id"], role,
                              history=d.get("history", []))
        else:
            result = agent_fn(question, session_id, user["id"], role)
    except Exception as e:
        return jsonify({"error": f"Agent error: {e}"}), 500

    # ── Gate 5: Output content filter ─────────────────────────
    summary = content_filter_output(result.get("summary", result.get("explanation", "")))
    result["summary"] = summary   # write back filtered summary

    # ── Gate 6: Log assistant turn ────────────────────────────
    log_conversation(session_id, user["id"], role, turn, "assistant", summary, agent_name)
    log_audit(session_id, user["id"], role, "QUERY",
              f"Agent={agent_name} tokens={result.get('prompt_tokens',0)+result.get('completion_tokens',0)}")

    # ── Hybrid response ───────────────────────────────────────
    # text_to_sql agent returns { summary, data, sql, explanation, ... }
    # All other agents return their existing JSON shape unchanged.
    response_payload = {
        "agent":      agent_name,
        "role":       role,
        "session_id": session_id,
        "result":     result,
    }
    # Promote summary + data to top-level for convenient frontend access
    if agent_name == "text_to_sql":
        response_payload["summary"] = summary
        response_payload["data"]    = result.get("data", [])

    return jsonify(response_payload)


# ── Cost dashboard ────────────────────────────────────────────
@app.route("/api/cost", methods=["GET"])
@login_required
def cost_dashboard():
    user = session["user"]
    role = user["role"]
    if not get_role(role).get("can_view_costs"):
        return jsonify({"error": "Access denied: cost data requires IT Admin or Infra Engineer role"}), 403

    conn = _db()
    # Per-model summary
    models = [dict(r) for r in conn.execute(
        """SELECT model, agent,
               COUNT(*) AS calls,
               SUM(prompt_tokens) AS total_prompt,
               SUM(completion_tokens) AS total_completion,
               ROUND(SUM(cost_usd),6) AS total_cost_usd
           FROM api_cost_log
           GROUP BY model, agent
           ORDER BY total_cost_usd DESC"""
    ).fetchall()]
    # Per-session summary
    sessions = [dict(r) for r in conn.execute(
        """SELECT session_id, user_id, user_role,
               COUNT(*) AS calls,
               ROUND(SUM(cost_usd),6) AS total_cost,
               MIN(created_at) AS started_at
           FROM api_cost_log
           GROUP BY session_id
           ORDER BY started_at DESC
           LIMIT 20"""
    ).fetchall()]
    # Recent log
    recent = [dict(r) for r in conn.execute(
        """SELECT model, agent, prompt_tokens, completion_tokens,
               ROUND(cost_usd,6) AS cost_usd, query_text, created_at
           FROM api_cost_log ORDER BY id DESC LIMIT 50"""
    ).fetchall()]
    # Grand total
    totals = dict(conn.execute(
        "SELECT COUNT(*) AS calls, ROUND(SUM(cost_usd),6) AS total FROM api_cost_log"
    ).fetchone())
    conn.close()
    return jsonify({"models": models, "sessions": sessions,
                    "recent": recent, "totals": totals})


# ── Audit log ─────────────────────────────────────────────────
@app.route("/api/audit", methods=["GET"])
@login_required
def audit():
    user = session["user"]
    if not get_role(user["role"]).get("can_manage"):
        return jsonify({"error": "IT Admin only"}), 403
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT 200"
    ).fetchall()]
    convs = [dict(r) for r in conn.execute(
        "SELECT * FROM conversation_log ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    conn.close()
    return jsonify({"audit_log": rows, "conversation_log": convs})


# ── Schema browser ────────────────────────────────────────────
@app.route("/api/schema", methods=["GET"])
@login_required
def schema():
    user  = session["user"]
    role  = user["role"]
    perms = get_role(role)
    conn  = sqlite3.connect(DB)
    cur   = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {}
    for (tname,) in cur.fetchall():
        if tname in ("conversation_log","audit_log") and not perms.get("can_manage"):
            continue
        if tname == "api_cost_log" and not perms.get("can_view_costs"):
            continue
        if perms["tables"] != ["*"] and tname not in perms["tables"]:
            continue
        cur.execute(f"PRAGMA table_info({tname})")
        cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {tname}")
        tables[tname] = {"columns": cols, "row_count": cur.fetchone()[0]}
    conn.close()
    return jsonify({"tables": tables, "role": role,
                    "accessible_tables": perms["tables"]})


# ── Stats (sidebar KPIs) ──────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
@login_required
def stats():
    conn = _db()
    s = {}
    s["open_incidents"]    = conn.execute("SELECT COUNT(*) FROM incidents WHERE status IN ('Open','In-Progress')").fetchone()[0]
    s["pending_access"]    = conn.execute("SELECT COUNT(*) FROM access_requests WHERE status='Pending'").fetchone()[0]
    s["open_alerts"]       = conn.execute("SELECT COUNT(*) FROM security_alerts WHERE status IN ('Open','Investigating')").fetchone()[0]
    s["right_size_count"]  = conn.execute("SELECT COUNT(*) FROM infra_assets WHERE cpu_util_pct<20 AND mem_util_pct<30 AND status='Running'").fetchone()[0]
    s["total_monthly_cost"]= conn.execute("SELECT ROUND(SUM(monthly_cost),0) FROM infra_assets WHERE status='Running'").fetchone()[0] or 0
    s["p1_open"]           = conn.execute("SELECT COUNT(*) FROM incidents WHERE priority='P1' AND status IN ('Open','In-Progress')").fetchone()[0]
    conn.close()
    return jsonify(s)


# ── Sample questions (role-aware) ─────────────────────────────
@app.route("/api/samples", methods=["GET"])
@login_required
def samples():
    role = session["user"]["role"]
    all_samples = {
        "IT Admin": [
            {"cat":"📊 KPI Overview",  "q":["How many open P1 incidents are there?","Show me API cost by model this session","Which team has the most unresolved incidents?"]},
            {"cat":"💰 Cost Analysis", "q":["Show total infra cost by cloud provider","Which assets are right-size candidates?","Average API cost per agent"]},
        ],
        "Architect": [
            {"cat":"🏗 ARB",           "q":["Draft an ARB document for Azure Service Bus migration","Is Redis Cache approved in enterprise architecture?","Show me all submitted ARB reviews this year"]},
            {"cat":"📊 Data",          "q":["Show ARB approval rate by department","Average prep hours for ARB submissions"]},
        ],
        "Security Ops": [
            {"cat":"🛡 Alerts",        "q":["Triage this alert: 450 failed logins from 10.0.0.5 in 5 minutes","Show P1 alert MTTD by detection source","Which alerts have been open > 24 hours?"]},
            {"cat":"🔐 Access",        "q":["Provision read access to Prod-DB-ReadOnly for E0042","Show all high-risk pending access requests"]},
        ],
        "Infra Engineer": [
            {"cat":"⚙ Infra",         "q":["Which Azure VMs are right-sizing candidates?","Show monthly cost breakdown by cloud and type","Run DR failover checklist for prod cluster"]},
        ],
        "Data Analyst": [
            {"cat":"📊 Analytics",    "q":["Show incident volume by team and priority","Access request SLA breach rate by department","Security alert MTTD trend this year"]},
        ],
        "IT Staff": [
            {"cat":"📋 Queries",      "q":["Show open incidents assigned to me","What is the status of ARB-00042?","How many P2 incidents are open this week?"]},
        ],
    }
    return jsonify({"samples": all_samples.get(role, all_samples["IT Staff"])})


# ── Demo users list (login page) ──────────────────────────────
@app.route("/api/demo-users", methods=["GET"])
def demo_users():
    return jsonify({"users": [
        {"email": u["email"], "password": u["password"],
         "name": u["name"], "role": u["role"]} for u in DEMO_USERS
    ]})


# ── Main page ─────────────────────────────────────────────────
@app.route("/")
def index():
    tmpl = os.path.join(BASE, "templates", "index.html")
    with open(tmpl, encoding="utf-8") as f:
        return render_template_string(f.read())


if __name__ == "__main__":
    # Bootstrap DB if needed
    if not os.path.exists(DB):
        print("Building database...")
        sys.path.insert(0, os.path.join(BASE, "data"))
        from seed import build
        build()

    print(f"ONDOL starting on http://localhost:{config.FLASK_PORT}")
    print("\n📋 Demo accounts:")
    for u in DEMO_USERS:
        print(f"   {u['email']:30s}  /  {u['password']:8s}  →  {u['role']}")
    print("\n⚠  Set OPENAI_API_KEY for live AI. Without it, demo stubs are used.\n")
    app.run(debug=config.DEBUG, port=config.FLASK_PORT, host="0.0.0.0")
