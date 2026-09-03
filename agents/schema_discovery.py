"""
agents/schema_discovery.py — Schema Discovery Agent

Like Databricks Genie's "understand your data" phase:
  1. Enumerate all tables the role can access
  2. Read actual column names, types, row counts
  3. Sample a few representative rows from each table
  4. Ask GPT-4o-mini to write a human-readable data profile
  5. Cache the profile per (role, db_path) to avoid repeated LLM calls

Output is injected into every SQL agent prompt so the LLM understands
what's actually in the database — not just column names, but real values.
"""

from __future__ import annotations
import json, sqlite3, time
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

# ── Simple in-memory cache: key = (role, table_filter_hash) ──
_profile_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 3600  # 1 hour — schema doesn't change during a session


# ──────────────────────────────────────────────────────────────
# Low-level DB introspection (no LLM, always fast)
# ──────────────────────────────────────────────────────────────

def _get_table_list(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def _get_columns(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [
        {"cid": r[0], "name": r[1], "type": r[2], "pk": bool(r[5])}
        for r in cur.fetchall()
    ]


def _get_row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _sample_rows(conn: sqlite3.Connection, table: str, n: int = 3) -> list[dict]:
    """
    Pull n random rows from table.
    Uses ORDER BY RANDOM() — fine for small (<50k row) SQLite datasets.
    """
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY RANDOM() LIMIT {n}")
    rows = [dict(r) for r in cur.fetchall()]
    conn.row_factory = None
    return rows


def _get_distinct_values(
    conn: sqlite3.Connection, table: str, col: str, limit: int = 8
) -> list[Any]:
    """Return distinct values for a column (useful for enum-like columns)."""
    cur = conn.execute(
        f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT {limit}"
    )
    return [r[0] for r in cur.fetchall()]


# ──────────────────────────────────────────────────────────────
# Build raw schema snapshot (structured, no LLM)
# ──────────────────────────────────────────────────────────────

ENUM_HINT_COLS = {
    "priority", "status", "severity", "risk_level", "access_level",
    "cloud", "type", "category", "source", "region", "role",
}


def build_raw_snapshot(
    db_path: Path,
    allowed_tables: list[str],
) -> dict[str, Any]:
    """
    Returns a structured dict describing every accessible table:
      {
        table_name: {
          row_count: int,
          columns: [{ name, type, pk, enum_values? }],
          sample_rows: [ {...}, ... ]
        }
      }
    """
    conn = sqlite3.connect(db_path)
    all_tables = _get_table_list(conn)

    snapshot: dict[str, Any] = {}
    for tname in all_tables:
        # Skip system / audit tables unless admin
        if tname in ("audit_log", "conversation_log") and allowed_tables != ["*"]:
            continue
        if allowed_tables != ["*"] and tname not in allowed_tables:
            continue

        cols = _get_columns(conn, tname)
        row_count = _get_row_count(conn, tname)

        # Enrich enum-like columns with distinct values
        for col in cols:
            if col["name"].lower() in ENUM_HINT_COLS or col["type"].upper() == "TEXT":
                vals = _get_distinct_values(conn, tname, col["name"])
                if 1 < len(vals) <= 8:
                    col["enum_values"] = vals

        # Sample rows (skip large tables' raw samples if row_count > 50k)
        samples = _sample_rows(conn, tname, n=3) if row_count < 50_000 else []

        snapshot[tname] = {
            "row_count": row_count,
            "columns": cols,
            "sample_rows": samples,
        }

    conn.close()
    return snapshot


# ──────────────────────────────────────────────────────────────
# Format snapshot as a compact string (injected into prompts)
# ──────────────────────────────────────────────────────────────

def format_snapshot_for_prompt(snapshot: dict[str, Any]) -> str:
    """
    Returns a compact, LLM-friendly schema description.
    Includes row counts, column types, enum values, and sample rows.
    """
    lines: list[str] = ["=== DATABASE SCHEMA (live introspection) ===\n"]

    for tname, info in snapshot.items():
        lines.append(f"TABLE: {tname}  ({info['row_count']:,} rows)")

        col_parts: list[str] = []
        for col in info["columns"]:
            part = f"  {col['name']} [{col['type']}]"
            if col.get("pk"):
                part += " PK"
            if col.get("enum_values"):
                vals = ", ".join(f"'{v}'" for v in col["enum_values"])
                part += f"  — values: {vals}"
            col_parts.append(part)
        lines.append("\n".join(col_parts))

        if info["sample_rows"]:
            lines.append("  Sample rows:")
            for row in info["sample_rows"][:2]:
                # Show only the first 6 fields to keep prompt short
                trimmed = {k: v for k, v in list(row.items())[:6]}
                lines.append(f"    {json.dumps(trimmed, default=str)}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# LLM-powered profiler (GPT-4o-mini) — optional enrichment
# ──────────────────────────────────────────────────────────────

def _call_mini(messages: list[dict]) -> str:
    """Thin wrapper — imports here to avoid circular import."""
    import urllib.request, urllib.error
    api_key = config.OPENAI_API_KEY
    if not api_key:
        return ""   # silently skip profiling if no key
    payload = json.dumps({
        "model": config.ROUTER_MODEL,
        "messages": messages,
        "max_tokens": 600,
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def generate_data_profile(snapshot: dict[str, Any], question: str) -> str:
    """
    Ask GPT-4o-mini to write a focused data profile for tables relevant
    to the user's question. Returns a short paragraph for injection.
    """
    cache_key = f"profile:{question[:80]}"
    entry = _profile_cache.get(cache_key)
    if entry and time.time() < entry[1]:
        return entry[0]

    # Find which tables are likely relevant
    q = question.lower()
    relevant_tables = [
        tname for tname in snapshot
        if any(part in q for part in tname.split("_"))
    ] or list(snapshot.keys())[:3]

    mini_snapshot = {k: snapshot[k] for k in relevant_tables if k in snapshot}
    schema_text = format_snapshot_for_prompt(mini_snapshot)

    prompt = f"""Given this database schema excerpt and the user question below,
write a short data profile (3-5 sentences) explaining:
1. Which tables are most relevant and why
2. Key columns the SQL should focus on
3. Any important data quirks (e.g. NULL patterns, enum values to use)

Schema:
{schema_text}

User question: {question}

Reply with ONLY the profile paragraph, no headers."""

    profile = _call_mini([{"role": "user", "content": prompt}])
    _profile_cache[cache_key] = (profile, time.time() + _CACHE_TTL)
    return profile


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────

_snapshot_cache: dict[str, tuple[dict, float]] = {}


def get_schema_context(
    db_path: Path,
    allowed_tables: list[str],
    question: str,
    use_llm_profile: bool = True,
) -> dict[str, Any]:
    """
    Full schema discovery for a question.
    Returns:
      {
        snapshot:       raw dict of table → columns + samples
        schema_text:    formatted string for LLM injection
        data_profile:   short GPT-4o-mini paragraph (if use_llm_profile=True)
        relevant_tables: list of table names likely needed
      }
    """
    cache_key = "|".join(sorted(allowed_tables)) if allowed_tables != ["*"] else "*"
    entry = _snapshot_cache.get(cache_key)
    now = time.time()

    if entry and now < entry[1]:
        snapshot = entry[0]
    else:
        snapshot = build_raw_snapshot(db_path, allowed_tables)
        _snapshot_cache[cache_key] = (snapshot, now + _CACHE_TTL)

    schema_text = format_snapshot_for_prompt(snapshot)

    # Detect relevant tables from question keywords
    q = question.lower()
    relevant_tables = [
        tname for tname in snapshot
        if any(kw in q for kw in tname.split("_") + [tname])
    ] or list(snapshot.keys())[:4]

    data_profile = ""
    if use_llm_profile and config.OPENAI_API_KEY:
        data_profile = generate_data_profile(snapshot, question)

    return {
        "snapshot": snapshot,
        "schema_text": schema_text,
        "data_profile": data_profile,
        "relevant_tables": relevant_tables,
    }
