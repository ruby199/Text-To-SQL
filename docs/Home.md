# ONDOL Wiki Home

**[English README](../README.md) | [한국어 README](../README.ko.md) | [Documentation index](README.md)**

ONDOL is a multi-agent AI platform for IT operations. It turns natural-language questions into governed data queries and domain-specific operational workflows.

## What the demo shows

- Supervisor-based intent routing
- Schema discovery before SQL generation
- Deterministic semantic-layer enrichment
- Specialist agents for SQL, architecture review, access, infrastructure, and security
- Independent evaluation with retry behavior
- Server-side RBAC, PII masking, content filters, and audit logging
- Real-time progress through Server-Sent Events

## Start here

1. [Architecture](Architecture.md) to understand the system boundary.
2. [Agents](Agents.md) to follow one request through the pipeline.
3. [Security](Security.md) to review the control gates.
4. [Data and Integration](Data-and-Integration.md) for the demo dataset and production path.
5. [API and Operations](API-and-Operations.md) to run and operate the application.

## Project materials

- [GIF demo](../demo/demo_-gif.gif)
- [Full video demo on YouTube](https://youtu.be/qUHExHGmuqs)
- [Presentation materials](../presentation/)

## Scope

The current implementation is a local, reproducible demo. SQLite and `data/seed.py` provide synthetic data for offline use; production data access should be introduced through an adapter and managed credentials.
