# Agents and Pipeline

**[Wiki Home](Home.md) | [Architecture](Architecture.md) | [Security](Security.md)**

## Pipeline

```text
Question
  -> Supervisor
  -> Schema Discovery (data tasks only)
  -> Semantic Layer
  -> Specialist
  -> Evaluator
  -> Response or retry
```

## Responsibilities

| Agent | Responsibility | Main output |
| --- | --- | --- |
| Supervisor | Intent, role context, execution plan | Plan and route |
| Schema Discovery | Tables, columns, row counts, sample values | Data profile |
| Semantic Layer | Glossary, KPI definitions, JOIN paths | Query context |
| Text-to-SQL | Read-only SQL generation and execution | SQL, rows, explanation |
| Architecture Review | ARB/RFC drafting and risk review | Document and ticket reference |
| Access Request | Access level and risk classification | Decision and provisioning groups |
| Infrastructure Ops | Right-sizing, cost, runbooks, DR | Findings and runbook |
| Security Triage | Severity, MITRE mapping, threat hunting | Playbook and response actions |
| Evaluator | Quality and safety gate | Score and retry decision |

## Evaluation loop

The evaluator scores SQL correctness, completeness, hallucination risk, and safety. A result below the configured threshold can be retried with an upgraded model. The retry limit is controlled by `pipeline/config.yaml`.

## Adding an agent

1. Add the implementation to `agents/`.
2. Add its prompt under `pipeline/prompts/`.
3. Register its model, tools, and limits in `pipeline/config.yaml`.
4. Add its RBAC roles to the pipeline configuration and server gate.
5. Return a stable JSON shape and add a focused test or smoke check.

Keep external side effects behind explicit tools and approval gates. Data queries should remain read-only by default.
