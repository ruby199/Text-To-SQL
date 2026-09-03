"""
data/seed.py — ONDOL demo dataset generator

This module is intentionally limited to local demos and automated tests. The
lists and probability weights below are synthetic fixtures, not production
configuration or business policy. Do not use this file to provision a live
database.

Production data-source direction:
1. Keep this SQLite seed path for offline demos and CI.
2. Add a repository adapter for Azure SQL/PostgreSQL and load credentials from
    environment variables or a managed identity; never place them in this file.
3. Add a Databricks SQL Warehouse adapter for analytics workloads, reusing the
    semantic-layer contract instead of copying these fixture tables.
4. Select the adapter from deployment configuration so agents do not need to
    know which database is behind the semantic layer.

완전히 현실적인 합성 IT 운영 데이터.
모든 PII는 합성 (SYN_ 접두사).

수정 사항 (v3):
- 인시던트: 제목↔카테고리 매핑 정확, 환경별 우선순위 현실적
- MTTD: P1 < P2 < P3 < P4 (심각할수록 빠르게 탐지)
- MTTR: resolved_at 계산 정확, severity 반영
- access_requests: Low > Medium > High 순으로 승인률 현실적
- infra_assets: SKU별 비용 현실적 (Azure D4s_v3 ~ $400/mo)
- KPI snapshots: 2년간 점진적 개선 트렌드 반영
- change_log: 실제 변경 유형별 현실적 내용
- arb_reviews: 기술별 승인률 차별화 (Azure > AWS > 오픈소스)
"""
import sqlite3, random, math
from datetime import date, timedelta, datetime

random.seed(42)
import os
BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "ondol.db")

# ── helpers ──────────────────────────────────────────────────
def rdate(start="2024-01-01", end="2025-03-31"):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return str(s + timedelta(days=random.randint(0, (e - s).days)))

def rdatetime(start="2024-01-01", end="2025-03-31"):
    d = rdate(start, end)
    h, m = random.randint(0, 23), random.randint(0, 59)
    return f"{d} {h:02d}:{m:02d}:00"

def add_hours(dt_str: str, hours: float) -> str:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return (dt + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

# ── Master data ──────────────────────────────────────────────
REGIONS  = ["KR", "SG", "HK", "JP", "AU", "IN"]
CLOUDS   = ["Azure", "AWS", "On-Prem"]
TEAMS    = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"]
ENVS     = ["Production", "Staging", "Development", "DR", "UAT"]

DEPT_DATA = [
    ("D001","IT Architecture",   "SYN_Kim_CJ",   "KR", 520000),
    ("D002","IT Security",       "SYN_Park_SH",  "SG", 430000),
    ("D003","IT Infrastructure", "SYN_Lee_JH",   "KR", 850000),
    ("D004","AD/IAM",            "SYN_Choi_MR",  "HK", 310000),
    ("D005","Data & Analytics",  "SYN_Jung_YS",  "JP", 370000),
    ("D006","IT Operations",     "SYN_Yoon_BK",  "AU", 470000),
    ("D007","Cloud Engineering", "SYN_Lim_TW",   "SG", 590000),
    ("D008","DevSecOps",         "SYN_Han_MK",   "KR", 480000),
]

ROLES_LIST = [
    "IT Admin", "Architect", "Security Ops", "Infra Engineer",
    "Data Analyst", "IT Staff", "Cloud Engineer", "DevOps Engineer",
]

# ── Incident title → category mapping (정확) ─────────────────
# (title_prefix, category, env_weights, prio_weights)
INC_TEMPLATES = [
    # Network
    ("VPN authentication failure for APAC users",           "Network",     [50,20,10,10,10], [5,20,45,30]),
    ("DNS resolution failure for internal services",         "Network",     [60,15,10,5,10],  [3,15,50,32]),
    ("Network latency spike on KR-SG peering link",          "Network",     [70,15,5,5,5],    [4,18,48,30]),
    ("Load balancer health check failing",                   "Network",     [60,20,10,5,5],   [5,20,45,30]),
    ("Firewall rule mismatch blocking ServiceNow integration","Network",     [65,15,10,5,5],   [3,15,45,37]),
    ("Azure VPN Gateway high latency",                       "Network",     [55,20,15,5,5],   [4,18,48,30]),
    # Application
    ("API gateway returning 502 Bad Gateway",                "Application", [80,10,5,3,2],    [8,22,40,30]),
    ("Production DB connection pool exhausted",              "Application", [90,5,3,1,1],     [12,28,35,25]),
    ("Application memory leak — heap dump requested",        "Application", [75,15,5,3,2],    [6,20,44,30]),
    ("Redis cluster split-brain detected",                   "Application", [85,10,3,1,1],    [10,25,38,27]),
    ("Kubernetes pod crash loop — OOMKilled",                "Application", [80,10,5,3,2],    [8,22,42,28]),
    ("Azure DevOps pipeline stuck in queue",                 "Application", [40,30,20,5,5],   [2,10,45,43]),
    # Security
    ("SSL certificate expired on api-gateway.example.com",   "Security",    [80,10,5,3,2],    [10,30,40,20]),
    ("Certificate authority root cert expired",              "Security",    [85,8,4,2,1],     [15,35,35,15]),
    ("Azure Key Vault access denied — RBAC misconfiguration","Security",    [70,15,8,4,3],    [8,22,45,25]),
    ("MFA service degraded — Okta outage",                   "Security",    [75,10,8,4,3],    [12,30,40,18]),
    ("SailPoint provisioning queue stuck",                   "Identity",    [60,20,10,5,5],   [5,15,45,35]),
    # Database
    ("PostgreSQL replication lag exceeding 30 seconds",      "Database",    [85,8,4,2,1],     [10,28,40,22]),
    ("CloudWatch alarm — RDS replica lag > 60s",             "Database",    [80,10,5,3,2],    [8,22,42,28]),
    ("Snowflake query timeout on analytics job",             "Database",    [50,30,15,3,2],   [3,12,45,40]),
    ("Elasticsearch cluster red status",                     "Database",    [75,15,7,2,1],    [8,20,42,30]),
    # Cloud
    ("Azure VM unresponsive after patch Tuesday",            "Cloud",       [75,10,5,5,5],    [6,20,44,30]),
    ("Azure Blob storage IOPS spike causing latency",        "Cloud",       [70,15,8,4,3],    [5,18,45,32]),
    ("AWS Lambda cold start degradation",                    "Cloud",       [55,25,15,3,2],   [4,15,46,35]),
    ("Azure AKS node pool scaling failure",                  "Cloud",       [75,10,8,4,3],    [7,20,43,30]),
    # Infrastructure / Hardware
    ("Server disk I/O saturation on prod host",              "Hardware",    [80,10,5,3,2],    [8,22,42,28]),
    ("UPS battery failure in KR datacenter",                 "Hardware",    [70,15,8,4,3],    [10,25,40,25]),
    # Monitoring / Observability
    ("Datadog agent not reporting metrics",                  "Monitoring",  [50,20,15,10,5],  [2,10,45,43]),
    ("Log pipeline lag exceeding 15 minutes",                "Monitoring",  [55,20,15,7,3],   [3,12,45,40]),
    ("Splunk indexer disk full — dropping events",           "Monitoring",  [60,15,15,7,3],   [5,15,45,35]),
    # CI/CD
    ("Terraform state lock not released",                    "CI/CD",      [30,30,30,5,5],   [2,8,40,50]),
    ("GitHub Enterprise disk usage at 95%",                  "CI/CD",      [30,25,30,10,5],  [3,10,42,45]),
    ("SonarQube quality gate blocking release",              "CI/CD",      [25,30,35,5,5],   [1,8,40,51]),
    # Identity
    ("AD domain controller sync failure",                    "Identity",   [65,15,10,5,5],   [6,18,45,31]),
    ("AD group policy not applying to new VMs",              "Identity",   [55,20,15,5,5],   [4,14,46,36]),
    # Email / Messaging
    ("Email relay outage — SMTP connection refused",         "Application", [60,15,15,7,3],   [5,15,45,35]),
]

ROOT_CAUSES = [
    "Configuration drift", "Capacity limit reached", "Software bug",
    "Human error", "Third-party service outage", "Expired credential",
    "Network partition", "Hardware failure", "Missing patch",
    "Race condition", "Memory leak", "Disk space exhaustion",
    "Certificate expiry", "DNS misconfiguration", "Dependency upgrade",
]

# ── Access Request Systems (현실적 데이터) ──────────────────
# (system_name, typical_access_level, typical_risk)
SYSTEMS_DETAILED = [
    ("Prod-DB-ReadOnly",          "Read",  "Low"),
    ("Snowflake-DataAnalyst",     "Read",  "Low"),
    ("Confluence-Read",           "Read",  "Low"),
    ("JIRA-Developer",            "Write", "Medium"),
    ("GitHub-Enterprise-Write",   "Write", "Medium"),
    ("ServiceNow-Operator",       "Write", "Medium"),
    ("Splunk-SOC",                "Read",  "Medium"),
    ("Datadog-ViewOnly",          "Read",  "Low"),
    ("PagerDuty-Responder",       "Write", "Medium"),
    ("Azure-DevOps-Contributor",  "Write", "Medium"),
    ("CrowdStrike-Analyst",       "Read",  "Medium"),
    ("Vault-ReadOnly",            "Read",  "Low"),
    ("Grafana-Editor",            "Write", "Medium"),
    ("AWS-S3-ReadOnly",           "Read",  "Low"),
    ("Okta-Admin",                "Admin", "High"),
    ("ServiceNow-Admin",          "Admin", "High"),
    ("Azure-Portal-Contributor",  "Write", "High"),
    ("GitHub-Enterprise-Admin",   "Admin", "High"),
    ("SailPoint-Admin",           "Admin", "High"),
    ("Prod-DB-Admin",             "Admin", "High"),
    ("CrowdStrike-API",           "Write", "High"),
    ("AWS-IAM-Admin",             "Admin", "High"),
    ("Terraform-Cloud-Operator",  "Write", "Medium"),
    ("M365-Admin",                "Admin", "High"),
]

JUSTIFICATIONS = [
    "Required for quarterly compliance audit",
    "Operational need — on-call rotation support",
    "Project onboarding for Q2 migration initiative",
    "Temporary elevated access for DR test exercise",
    "Standard role provisioning for new team member",
    "SOC investigation support — active incident",
    "Data migration project access",
    "Pen testing engagement support",
    "Audit preparation — evidence collection",
    "Automated pipeline service account",
    "Vendor onboarding — temporary project access",
    "Platform upgrade support role",
]

# ── ARB Tech → Approval tendency ──────────────────────────────
TECH_APPROVAL = {
    "Azure Service Bus":      0.72,
    "Azure API Management":   0.75,
    "Azure Functions":        0.68,
    "Azure Synapse":          0.65,
    "AKS":                    0.70,
    "Azure Event Hub":        0.73,
    "Cosmos DB":              0.62,
    "Terraform 1.7":          0.80,
    "Vault by HashiCorp":     0.78,
    "ArgoCD":                 0.71,
    "Prometheus+Grafana":     0.76,
    "AWS Lambda":             0.55,  # Not preferred (Azure-first)
    "AWS SQS":                0.50,
    "Apache Kafka":           0.58,
    "Kubernetes 1.29":        0.65,
    "Redis Enterprise":       0.60,
    "Elasticsearch 8":        0.57,
    "PostgreSQL 16":          0.63,
    "Databricks":             0.68,
    "Istio Service Mesh":     0.52,  # Complex, often rejected
}

# ── Infra SKU → realistic cost/month (USD) ────────────────────
SKU_COST = {
    # Azure VMs
    "D2s_v3":   180,  "D4s_v3":   360,  "D8s_v3":   720,
    "E4s_v3":   320,  "B2ms":      60,  "F4s_v2":   240,
    # AKS / ECS
    "AKS-Node-4vCPU": 520, "AKS-Node-2vCPU": 260,
    "ECS-Task": 120,  "GKE-n1-std": 110,
    # Network
    "Azure-FW-Premium": 1500, "WAF-v2": 300,
    "VPN-GW-VpnGw2": 500, "ALB-Standard": 200,
    # Storage
    "StorageV2-LRS": 80, "StorageV2-GRS": 140,
    "S3-Standard": 70, "NFS-Premium": 250,
    # Database
    "PostgreSQL-GP-4vCPU": 1200, "Redis-C2": 800,
    "Cosmos-RU-10k": 2000, "MySQL-GP-8": 900,
}

ASSET_NAMES = {
    "VM":        ["web-prod", "api-gw", "batch-worker", "db-replica", "cache-node",
                  "bastion", "build-agent", "app-server", "report-runner", "etl-host"],
    "Container": ["nginx-lb", "app-service", "auth-svc", "data-pipeline",
                  "scheduler", "worker", "api-proxy", "notification-svc"],
    "Network":   ["core-switch", "firewall-01", "vpn-gw", "waf", "load-balancer", "nat-gw"],
    "Storage":   ["blob-prod", "nfs-share", "backup-vault", "archive-tier", "log-bucket"],
    "Database":  ["postgres-primary", "redis-cluster", "cosmos-db", "mysql-replica", "elasticsearch"],
}

MITRE_MAP = {
    "Brute-Force":          ("T1110", "Brute Force"),
    "Lateral-Movement":     ("T1021", "Remote Services"),
    "Data-Exfiltration":    ("T1048", "Exfiltration Over Alt Protocol"),
    "Privilege-Escalation": ("T1068", "Exploitation for Privilege Escalation"),
    "Anomalous-Login":      ("T1078", "Valid Accounts"),
    "Malware":              ("T1059", "Command and Scripting Interpreter"),
    "Phishing":             ("T1566", "Phishing"),
    "Ransomware":           ("T1486", "Data Encrypted for Impact"),
    "C2-Beacon":            ("T1071", "Application Layer Protocol"),
    "Credential-Stuffing":  ("T1110.004", "Credential Stuffing"),
    "Supply-Chain":         ("T1195", "Supply Chain Compromise"),
    "Policy-Violation":     ("T1078.003", "Cloud Accounts"),
    "Insider-Threat":       ("T1078.001", "Default Accounts"),
    "DDoS":                 ("T1498", "Network Denial of Service"),
    "SQL-Injection":        ("T1190", "Exploit Public-Facing Application"),
}

ALERT_SOURCES = ["CrowdStrike", "Splunk", "Defender", "Tenable", "Darktrace", "SentinelOne", "Manual"]

# MTTD base (minutes) by source — CrowdStrike fastest, Manual slowest
SOURCE_MTTD = {
    "CrowdStrike": 3,  "Darktrace": 5,  "SentinelOne": 6,
    "Defender": 8,     "Splunk": 12,    "Tenable": 25,  "Manual": 90,
}

# MTTD multiplier by severity — P1 detected fast (SOC alert immediately)
SEV_MTTD_MULT = {"P1": 0.4, "P2": 0.8, "P3": 1.5, "P4": 3.0}

# MTTR base (hours) by severity
SEV_MTTR_BASE = {"P1": 2.0, "P2": 8.0, "P3": 24.0, "P4": 72.0}


def build():
    """Build the disposable SQLite fixture used by the local demo.

    Production deployments should provision their schema through migrations
    and connect through a data-source adapter, not call this function.
    """
    conn = sqlite3.connect(DB)
    c    = conn.cursor()

    c.executescript("""
    DROP TABLE IF EXISTS employees; DROP TABLE IF EXISTS departments;
    DROP TABLE IF EXISTS incidents; DROP TABLE IF EXISTS access_requests;
    DROP TABLE IF EXISTS arb_reviews; DROP TABLE IF EXISTS infra_assets;
    DROP TABLE IF EXISTS security_alerts; DROP TABLE IF EXISTS api_cost_log;
    DROP TABLE IF EXISTS audit_log; DROP TABLE IF EXISTS conversation_log;
    DROP TABLE IF EXISTS change_log; DROP TABLE IF EXISTS cost_forecast;
    DROP TABLE IF EXISTS kpi_snapshots;

    CREATE TABLE departments(
        dept_id TEXT PRIMARY KEY, name TEXT, head TEXT, region TEXT, budget INTEGER
    );
    CREATE TABLE employees(
        emp_id TEXT PRIMARY KEY, name TEXT, email TEXT, dept_id TEXT,
        role TEXT, region TEXT, hire_date TEXT, is_active INTEGER DEFAULT 1,
        manager_id TEXT, cost_centre TEXT
    );
    CREATE TABLE incidents(
        incident_id TEXT PRIMARY KEY, title TEXT, team TEXT,
        priority TEXT, status TEXT, created_at TEXT, resolved_at TEXT,
        resolution_hours REAL, assignee_id TEXT, dept_id TEXT,
        category TEXT, root_cause TEXT, environment TEXT
    );
    CREATE TABLE access_requests(
        req_id TEXT PRIMARY KEY, requestor_id TEXT, target_system TEXT,
        access_level TEXT, status TEXT, submitted_at TEXT, decided_at TEXT,
        sla_hours REAL, dept_id TEXT, risk_level TEXT,
        jit_access INTEGER DEFAULT 0, expiry_days INTEGER DEFAULT 0,
        justification TEXT
    );
    CREATE TABLE arb_reviews(
        arb_id TEXT PRIMARY KEY, project_name TEXT, submitter_id TEXT,
        status TEXT, submitted_at TEXT, decided_at TEXT, prep_hours REAL,
        technology TEXT, dept_id TEXT, data_classification TEXT,
        estimated_cost_usd INTEGER, approved_by TEXT
    );
    CREATE TABLE infra_assets(
        asset_id TEXT PRIMARY KEY, name TEXT, type TEXT, cloud TEXT,
        region TEXT, monthly_cost REAL, cpu_util_pct REAL, mem_util_pct REAL,
        status TEXT, dept_id TEXT, last_reviewed TEXT,
        sku TEXT, created_date TEXT, patched_date TEXT, criticality TEXT
    );
    CREATE TABLE security_alerts(
        alert_id TEXT PRIMARY KEY, title TEXT, severity TEXT,
        status TEXT, source TEXT, created_at TEXT, resolved_at TEXT,
        mttd_minutes REAL, mttr_minutes REAL, category TEXT,
        assignee_id TEXT, mitre_technique TEXT, false_positive INTEGER DEFAULT 0,
        affected_assets INTEGER DEFAULT 1
    );
    CREATE TABLE change_log(
        change_id TEXT PRIMARY KEY, asset_id TEXT, change_type TEXT,
        description TEXT, changed_by TEXT, changed_at TEXT,
        reason TEXT, approved_by TEXT, outcome TEXT
    );
    CREATE TABLE cost_forecast(
        forecast_id TEXT PRIMARY KEY, dept_id TEXT, month TEXT,
        actual_cost REAL, forecasted_cost REAL, variance_pct REAL,
        resource_type TEXT
    );
    CREATE TABLE kpi_snapshots(
        snapshot_id TEXT PRIMARY KEY, snapshot_date TEXT,
        open_p1_incidents INTEGER, open_p2_incidents INTEGER,
        avg_mttd_minutes REAL, avg_mttr_minutes REAL,
        sla_breach_rate_pct REAL, arb_approval_rate_pct REAL,
        total_infra_cost_usd REAL, right_size_count INTEGER,
        pending_access_count INTEGER, open_alerts_p1 INTEGER
    );
    CREATE TABLE api_cost_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT,
        user_role TEXT, agent TEXT, model TEXT, prompt_tokens INTEGER,
        completion_tokens INTEGER, cost_usd REAL, query_text TEXT, created_at TEXT
    );
    CREATE TABLE audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT,
        user_role TEXT, action TEXT, detail TEXT, ip_address TEXT, created_at TEXT
    );
    CREATE TABLE conversation_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT,
        user_role TEXT, turn INTEGER, role TEXT, content TEXT, agent TEXT,
        created_at TEXT, retained_until TEXT
    );
    """)

    # ── departments ──────────────────────────────────────────
    c.executemany("INSERT INTO departments VALUES (?,?,?,?,?)", DEPT_DATA)
    dept_ids = [d[0] for d in DEPT_DATA]

    # ── employees (150) ──────────────────────────────────────
    emps, mgr_pool = [], []
    for i in range(1, 151):
        dept  = random.choice(DEPT_DATA)
        role  = random.choice(ROLES_LIST)
        eid   = f"E{i:04d}"
        mgr   = random.choice(mgr_pool) if mgr_pool else None
        emps.append((eid, f"SYN_User_{i:04d}", f"syn_user_{i:04d}@ondol.example.com",
                     dept[0], role, random.choice(REGIONS),
                     rdate("2017-01-01", "2024-06-30"), 1, mgr, f"CC-{dept[0]}"))
        if i % 12 == 0:
            mgr_pool.append(eid)
    c.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?)", emps)
    emp_ids = [e[0] for e in emps]

    # ── incidents (1,000) ────────────────────────────────────
    incs = []
    for i in range(1, 1001):
        tmpl   = random.choice(INC_TEMPLATES)
        title, category, env_w, prio_w = tmpl
        prio   = random.choices(["P1","P2","P3","P4"], weights=prio_w)[0]
        env    = random.choices(ENVS, weights=env_w)[0]
        # P1/P2 mostly Production; P3/P4 spread across envs
        if prio == "P1":
            env = random.choices(["Production","DR"], weights=[90,10])[0]
        elif prio == "P2":
            env = random.choices(["Production","Staging","DR"], weights=[75,15,10])[0]

        status = random.choices(
            ["Open","In-Progress","Resolved","Closed"],
            weights={"P1":[5,10,40,45],"P2":[8,12,40,40],"P3":[10,15,38,37],"P4":[12,18,35,35]}[prio]
        )[0]

        created  = rdatetime("2024-01-01", "2025-03-31")
        res_h, res_at = None, None
        if status in ("Resolved", "Closed"):
            # Realistic resolution time by priority
            base_h = {"P1": 2.0, "P2": 12.0, "P3": 36.0, "P4": 96.0}[prio]
            res_h  = round(random.uniform(base_h * 0.4, base_h * 2.2), 2)
            res_at = add_hours(created, res_h)

        incs.append((
            f"INC{i:05d}", title,
            random.choice(TEAMS), prio, status,
            created, res_at, res_h,
            random.choice(emp_ids), random.choice(dept_ids),
            category,
            random.choice(ROOT_CAUSES) if status in ("Resolved","Closed") else None,
            env
        ))
    c.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", incs)

    # ── access_requests (600) ────────────────────────────────
    arqs = []
    for i in range(1, 601):
        sys_info = random.choice(SYSTEMS_DETAILED)
        sys_name, base_level, base_risk = sys_info

        # Small chance of escalated level
        al   = base_level if random.random() < 0.85 else random.choice(["Read","Write","Admin"])
        risk = "High" if al == "Admin" else base_risk

        # Approval rate: Low=85%, Medium=65%, High=55%
        approval_rate = {"Low": 0.85, "Medium": 0.65, "High": 0.55}[risk]
        status_weights = {
            "Low":    [10, int(approval_rate*80), int((1-approval_rate)*60), 10],
            "Medium": [15, int(approval_rate*75), int((1-approval_rate)*65), 10],
            "High":   [18, int(approval_rate*70), int((1-approval_rate)*70), 12],
        }
        status = random.choices(
            ["Pending","Approved","Rejected","Expired"],
            weights=status_weights[risk]
        )[0]

        sub_at = rdatetime("2024-01-01", "2025-03-31")
        # SLA = hours from submission to decision
        if status == "Approved":
            # Low risk faster approval
            base_sla = {"Low": 4, "Medium": 12, "High": 24}[risk]
            sla_h = round(random.uniform(base_sla * 0.5, base_sla * 3), 1)
        elif status == "Rejected":
            sla_h = round(random.uniform(24, 96), 1)
        else:
            sla_h = round(random.uniform(2, 48), 1)

        decided_at = add_hours(sub_at, sla_h) if status in ("Approved","Rejected") else None
        jit        = 1 if risk in ("Medium","High") and random.random() < 0.55 else 0
        expiry     = 30 if risk=="High" else (7 if jit else 0)

        arqs.append((
            f"AR{i:05d}", random.choice(emp_ids), sys_name, al, status,
            sub_at, decided_at, sla_h, random.choice(dept_ids), risk,
            jit, expiry, random.choice(JUSTIFICATIONS)
        ))
    c.executemany("INSERT INTO access_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", arqs)

    # ── arb_reviews (250) ────────────────────────────────────
    TECH_LIST  = list(TECH_APPROVAL.keys())
    DATA_CLASSES = ["Internal","Internal","Internal","Confidential","Public"]
    PROJECT_NAMES = ["Athena","Poseidon","Hermes","Apollo","Artemis","Zeus","Ares",
                     "Hera","Demeter","Hephaestus","Dionysus","Hestia"]
    arbs = []
    for i in range(1, 251):
        tech   = random.choice(TECH_LIST)
        ap_rate = TECH_APPROVAL[tech]
        # Adjust for data classification
        dc     = random.choice(DATA_CLASSES)
        if dc == "Confidential":
            ap_rate *= 0.85
        status = random.choices(
            ["Draft","Submitted","Approved","Rejected"],
            weights=[10, 25, int(ap_rate*55), int((1-ap_rate)*55)]
        )[0]
        prep_h = round(random.uniform(2, 10), 2)
        # Azure gets higher budget, open-source lower
        est_cost = random.randint(20000, 400000) if "Azure" in tech else \
                   random.randint(5000, 150000)
        submitter = random.choice(emp_ids)
        approved_by = random.choice(emp_ids) if status == "Approved" else None
        submitted = rdatetime("2024-01-01", "2025-03-31")
        decided   = add_hours(submitted, random.uniform(24, 240)) if status in ("Approved","Rejected") else None
        arbs.append((
            f"ARB{i:05d}",
            f"Project_{random.choice(PROJECT_NAMES)}_{i:03d}",
            submitter, status, submitted, decided,
            prep_h, tech, random.choice(dept_ids), dc, est_cost, approved_by
        ))
    c.executemany("INSERT INTO arb_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", arbs)

    # ── infra_assets (400) ────────────────────────────────────
    # Cloud distribution: Azure 55%, AWS 30%, On-Prem 15%
    CRITICALITIES = ["Critical","High","Medium","Low"]
    SKUS = {
        "VM":        ["D2s_v3","D4s_v3","D8s_v3","E4s_v3","B2ms","F4s_v2"],
        "Container": ["AKS-Node-4vCPU","AKS-Node-2vCPU","ECS-Task","GKE-n1-std"],
        "Network":   ["Azure-FW-Premium","WAF-v2","VPN-GW-VpnGw2","ALB-Standard"],
        "Storage":   ["StorageV2-LRS","StorageV2-GRS","S3-Standard","NFS-Premium"],
        "Database":  ["PostgreSQL-GP-4vCPU","Redis-C2","Cosmos-RU-10k","MySQL-GP-8"],
    }
    assets, asset_ids = [], []
    atype_list = ["VM"]*18 + ["Container"]*6 + ["Network"]*4 + ["Storage"]*4 + ["Database"]*4  # relative counts
    for i in range(1, 401):
        atype = random.choice(atype_list)
        cloud = random.choices(CLOUDS, weights=[55,30,15])[0]
        sku   = random.choice(SKUS[atype])
        # Base cost from SKU, add variance
        base_cost = SKU_COST.get(sku, 500)
        monthly   = round(base_cost * random.uniform(0.8, 1.3), 0)

        # Utilisation patterns: realistic distribution
        pattern = random.choices(
            ["healthy","underutil","overutil","critical","idle"],
            weights=[45, 25, 15, 10, 5]
        )[0]
        if pattern == "healthy":
            cpu = random.uniform(30, 65);   mem = random.uniform(35, 60)
        elif pattern == "underutil":
            cpu = random.uniform(3, 18);    mem = random.uniform(8, 28)
        elif pattern == "overutil":
            cpu = random.uniform(75, 92);   mem = random.uniform(70, 88)
        elif pattern == "critical":
            cpu = random.uniform(88, 99);   mem = random.uniform(85, 98)
        else:  # idle
            cpu = random.uniform(0, 5);     mem = random.uniform(2, 10)

        status = "Stopped" if pattern == "idle" else \
                 random.choices(["Running","Stopped","Decommissioned"], weights=[78,15,7])[0]
        crit   = "Critical" if pattern == "critical" else \
                 "High" if pattern == "overutil" else \
                 "Low" if pattern in ("underutil","idle") else \
                 random.choices(CRITICALITIES, weights=[10,30,45,15])[0]
        nm = random.choice(ASSET_NAMES[atype])
        aid = f"ASSET{i:05d}"
        assets.append((
            aid, f"{cloud.lower()[:3]}-{nm}-{i:03d}",
            atype, cloud, random.choice(REGIONS),
            monthly, round(cpu,1), round(mem,1), status,
            random.choice(dept_ids), rdate("2024-09-01","2025-03-01"),
            sku, rdate("2021-01-01","2024-01-01"),
            rdate("2024-10-01","2025-03-01"), crit
        ))
        asset_ids.append(aid)
    c.executemany("INSERT INTO infra_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", assets)

    # ── security_alerts (800) ────────────────────────────────
    ALERT_CATS  = list(MITRE_MAP.keys())
    alerts = []
    for i in range(1, 801):
        cat    = random.choice(ALERT_CATS)
        sev    = random.choices(["P1","P2","P3","P4"], weights=[7,20,40,33])[0]
        src    = random.choice(ALERT_SOURCES)
        created = rdatetime("2024-01-01","2025-03-31")

        # MTTD: severity-adjusted, source-adjusted
        mttd_base = SOURCE_MTTD[src]
        mttd_mult = SEV_MTTD_MULT[sev]
        mttd = round(random.uniform(
            mttd_base * mttd_mult * 0.5,
            mttd_base * mttd_mult * 2.5
        ), 1)
        mttd = max(1.0, mttd)  # minimum 1 minute

        # Status: P1/P2 mostly investigated/resolved
        status_w = {"P1":[5,25,65,5],"P2":[10,20,58,12],"P3":[15,18,52,15],"P4":[12,10,40,38]}[sev]
        status = random.choices(["Open","Investigating","Resolved","False-Positive"],
                                weights=status_w)[0]

        mttr, res_at = None, None
        if status == "Resolved":
            mttr_base = SEV_MTTR_BASE[sev] * 60  # convert to minutes
            mttr = round(random.uniform(mttr_base * 0.4, mttr_base * 2.5), 1)
            res_at = add_hours(created, mttr/60)
        fp = 1 if status == "False-Positive" else 0

        # MITRE
        tid, tname = MITRE_MAP.get(cat, ("T0000","Unknown"))
        mitre_str  = f"{tid} — {tname}"

        affected = random.randint(1, 50) if sev == "P1" else \
                   random.randint(1, 20) if sev == "P2" else \
                   random.randint(1, 8)

        alerts.append((
            f"ALERT{i:05d}",
            f"[{sev}] {cat.replace('-',' ')} detected via {src}",
            sev, status, src, created, res_at, mttd, mttr,
            cat, random.choice(emp_ids), mitre_str, fp, affected
        ))
    c.executemany("INSERT INTO security_alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", alerts)

    # ── change_log (300) ─────────────────────────────────────
    CHANGE_TYPES = {
        "RESIZE":         ("Downsized {} to smaller SKU for cost optimisation", "Cost optimisation"),
        "PATCH":          ("Applied OS/security patch {} to address CVE", "Security compliance"),
        "DECOMMISSION":   ("Decommissioned unused {} after 90 days idle", "Resource cleanup"),
        "DEPLOY":         ("Deployed new version of {} application", "Feature release"),
        "CONFIG_CHANGE":  ("Updated configuration on {} — firewall rule update", "Security hardening"),
        "SCALE_OUT":      ("Scaled out {} to handle increased load", "Capacity management"),
        "BACKUP_RESTORE": ("Restored {} from backup after data corruption", "Incident recovery"),
        "MIGRATION":      ("Migrated {} from on-prem to Azure", "Cloud migration"),
    }
    changes = []
    for i in range(1, 301):
        asset = random.choice(asset_ids)
        ct, (desc_tmpl, reason) = random.choice(list(CHANGE_TYPES.items()))
        desc = desc_tmpl.format(asset)
        outcome = random.choices(
            ["Success","Success","Success","Failed","Rolled-back"],
            weights=[70,70,70,10,10]
        )[0]
        changes.append((
            f"CHG{i:05d}", asset, ct, desc,
            random.choice(emp_ids), rdatetime("2024-01-01","2025-03-31"),
            reason, random.choice(emp_ids), outcome
        ))
    c.executemany("INSERT INTO change_log VALUES (?,?,?,?,?,?,?,?,?)", changes)

    # ── cost_forecast (24 months × 8 depts × 4 types) ────────
    RTYPES = ["Compute","Storage","Network","Database"]
    forecasts, fid = [], 1
    for dept in DEPT_DATA:
        # Base monthly cost varies by dept size
        dept_base = dept[4] / 12 / len(RTYPES)
        for mo in range(24):
            m = (date(2024, 1, 1) + timedelta(days=30*mo)).strftime("%Y-%m")
            for rtype in RTYPES:
                # Add slight growth trend
                growth = 1 + (mo * 0.005)
                actual = round(dept_base * growth * random.uniform(0.8, 1.3), 0)
                # Forecast is close but not perfect
                fore   = round(actual * random.uniform(0.88, 1.12), 0)
                var    = round((actual - fore) / max(fore, 1) * 100, 1)
                forecasts.append((f"FC{fid:05d}", dept[0], m, actual, fore, var, rtype))
                fid += 1
    c.executemany("INSERT INTO cost_forecast VALUES (?,?,?,?,?,?,?)", forecasts)

    # ── kpi_snapshots (weekly, 2yr — showing improvement trend) ─
    # Simulate gradual improvement over 2 years
    snapshots, sid = [], 1
    snap_date = date(2024, 1, 1)
    # Starting values
    p1_base, mttd_base, sla_breach_base = 18, 42, 28.0
    arb_approval_base, rs_count_base    = 58.0, 28
    infra_cost_base = 235000

    while snap_date <= date(2026, 1, 1):
        # Progress 0→1 over 2 years
        progress = (snap_date - date(2024, 1, 1)).days / 730
        noise    = lambda x: x * random.uniform(0.9, 1.1)

        p1   = max(3, int(p1_base * (1 - progress * 0.4) + noise(2)))
        mttd = max(8, noise(mttd_base * (1 - progress * 0.35)))
        sla  = max(5, noise(sla_breach_base * (1 - progress * 0.45)))
        arb  = min(88, noise(arb_approval_base * (1 + progress * 0.2)))
        infra_cost = noise(infra_cost_base * (1 + progress * 0.08))
        rs   = max(3, int(rs_count_base * (1 - progress * 0.3) + noise(3)))
        pending_ac = max(8, int(60 * (1 - progress * 0.3) + noise(5)))
        p1_al = max(1, int(8 * (1 - progress * 0.4) + noise(1)))
        p2_open = max(10, int(50 * (1 - progress * 0.35) + noise(5)))
        mttr = max(30, noise(240 * (1 - progress * 0.3)))

        snapshots.append((
            f"KPI{sid:05d}",
            str(snap_date),
            p1, p2_open,
            round(mttd, 1), round(mttr, 1),
            round(sla, 1), round(arb, 1),
            round(infra_cost, 0), rs, pending_ac, p1_al
        ))
        snap_date += timedelta(weeks=1)
        sid += 1
    c.executemany("INSERT INTO kpi_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", snapshots)

    conn.commit()
    conn.close()

    counts = {
        "departments": len(DEPT_DATA), "employees": len(emps),
        "incidents":   len(incs),      "access_requests": len(arqs),
        "arb_reviews": len(arbs),      "infra_assets":    len(assets),
        "security_alerts": len(alerts),"change_log":      len(changes),
        "cost_forecast": len(forecasts),"kpi_snapshots":  len(snapshots),
    }
    total = sum(counts.values())
    print(f"✅ ondol.db built — {total:,} total rows:")
    for k, v in counts.items():
        print(f"   {k:22s}: {v:>5,} rows")
    return DB

if __name__ == "__main__":
    build()
