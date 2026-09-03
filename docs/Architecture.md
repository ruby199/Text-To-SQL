# Architecture

**[Wiki Home](Home.md) | [Agents](Agents.md) | [Security](Security.md)**

## System boundary

```text
Browser SPA
    |
    v
Flask API (app.py)
    |
    v
Security gates (core/rbac.py)
    |
    v
Pipeline orchestrator (pipeline/graph.py)
    |
    +--> Supervisor
    +--> Schema Discovery
    +--> Semantic Layer
    +--> Specialist agent
    +--> Evaluator
    |
    v
SQLite demo data / future data-source adapter
```

## Request lifecycle

1. The browser authenticates through `/api/login` and receives a session cookie.
2. `/api/ask` validates the question and applies the input content filter.
3. The request is logged with PII handling before agent execution.
4. The router selects an agent while the RBAC gate checks role access.
5. Data requests discover the accessible schema and semantic context before SQL generation.
6. The specialist executes the domain task and returns a structured result.
7. Output is filtered, evaluated, logged, and returned to the browser.

## Technology boundaries

| Boundary | Current implementation | Production direction |
| --- | --- | --- |
| Web | Flask and vanilla JavaScript | WSGI/ASGI deployment behind TLS |
| Orchestration | LangGraph-style standard-library graph | LangGraph with durable checkpointing |
| Data | Local SQLite fixture | Azure SQL/PostgreSQL and Databricks adapters |
| Configuration | YAML plus environment variables | Secret manager and deployment configuration |
| Streaming | Server-Sent Events | Same pattern behind a production reverse proxy |

## Design principle

Agents should depend on the semantic-layer contract, not on a specific database vendor. This keeps prompts and business terminology stable when the underlying data source changes.
