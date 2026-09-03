# API and Operations

**[Wiki Home](Home.md) | [Architecture](Architecture.md) | [Security](Security.md)**

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python data/seed.py
python app.py
```

The default local URL is `http://localhost:5001`. Set `OPENAI_API_KEY`, `SECRET_KEY`, `FLASK_PORT`, and `DEBUG` in `.env` as needed. Without an API key, supported demo paths use stub responses.

## Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Web application |
| POST | `/api/login` | Authenticate a demo user |
| POST | `/api/logout` | Clear the session |
| POST | `/api/ask` | Execute a natural-language request |
| GET | `/api/stats` | Dashboard metrics |
| GET | `/api/schema` | Role-filtered schema browser |
| GET | `/api/cost` | Cost dashboard for permitted roles |
| GET | `/api/audit` | Audit and conversation logs for administrators |

## Release checklist

- Run Python syntax checks and focused tests.
- Confirm `.env`, databases, logs, and caches are ignored.
- Rotate any key that was ever exposed locally.
- Set a strong `SECRET_KEY` and `DEBUG=false`.
- Replace demo authentication and seed data before production use.
- Configure the intended Azure or Databricks adapter.
- Validate RBAC, PII masking, SQL read-only behavior, retention, and audit access.
