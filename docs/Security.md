# Security and Compliance

**[Wiki Home](Home.md) | [Architecture](Architecture.md) | [API and Operations](API-and-Operations.md)**

## Control gates

| Gate | Control |
| --- | --- |
| Input | PII removal and prompt/content filtering |
| Identity | Session authentication and role lookup |
| Authorization | Agent and table allowlists in the server |
| SQL | Read-only validation and maximum result rows |
| Output | PII masking for roles without visibility |
| Audit | Login, access denial, query, cost, and conversation events |

## Production requirements

- Replace demo credentials with SSO/IAM and hashed password storage.
- Set a unique secret key through a secret manager or environment variable.
- Keep `DEBUG=false` and place the app behind TLS and a production WSGI server.
- Use managed identity or workload identity for Azure resources.
- Store logs and audit records in a protected, access-controlled sink.
- Review retention, PII masking, network egress, and model provider policies before deployment.
- Put side-effecting operations such as access provisioning behind approval and idempotency controls.

## Data classification

The checked-in dataset is synthetic. A production adapter must enforce classification and row/column policies before data reaches an LLM. Do not send secrets, raw credentials, or unrestricted personal data in prompts.
