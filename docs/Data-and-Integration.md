# Data and Integration

**[Wiki Home](Home.md) | [Architecture](Architecture.md) | [Security](Security.md)**

## Current demo path

`data/seed.py` creates a deterministic SQLite fixture for local demos and CI. The hardcoded rows, distributions, and weights are synthetic fixtures, not production policy. The generated `data/ondol.db` is ignored by Git.

## Recommended production adapters

### Azure SQL or PostgreSQL

Use an adapter that implements the existing query and schema-discovery contract. Load connection details from environment variables, a secret manager, or managed identity. Use migrations, connection pooling, least-privilege database users, and read replicas where appropriate.

### Databricks SQL Warehouse

Use a separate analytics adapter for warehouse queries. Reuse the semantic-layer vocabulary and role policy, while translating SQL dialect details at the adapter boundary. Apply catalog, schema, row, and column permissions before query execution.

## Adapter boundary

```text
Agent and prompts
       |
Semantic Layer contract
       |
Data-source adapter
   /        |        \
SQLite   Azure SQL   Databricks SQL
 demo    operations   analytics
```

The deployment configuration should select the adapter without changing agent prompts. `seed.py` should remain available only for offline development and test environments.
