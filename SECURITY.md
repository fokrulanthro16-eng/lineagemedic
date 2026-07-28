# Security policy

## Reporting a vulnerability

Open a GitHub security advisory on this repository, or a private issue if
advisories are unavailable. Please do not open a public issue for an
unpatched vulnerability.

Include what you did, what happened, and what you expected. A proof of concept
is welcome but not required.

## Handling of credentials

LineageMedic reads credentials from the environment and never from a committed
file.

| Variable | Purpose |
|----------|---------|
| `DATAHUB_GMS_TOKEN` | DataHub API token. Optional; unset in fixture mode. |
| `DATAHUB_GMS_URL` | DataHub GMS endpoint. |
| `MCP_SERVER_URL` | DataHub MCP Server endpoint. |

Rules the code follows, each enforced rather than assumed:

- **Tokens are never returned by an endpoint.** `GET /status/datahub` and
  `GET /` report `token_configured: true|false` — a boolean derived with
  `bool(...)`, never the value. `test_root_banner_redacts_configuration`
  asserts the token key is absent from the response entirely.
- **Tokens are never logged.** The request-logging middleware records method,
  path, status, and duration. It does not log headers, query strings, or bodies.
- **No credential is required to run the demo.** Fixture mode needs no token,
  no account, and no network access.

If you find a code path that prints, returns, or logs a credential, treat it as
a vulnerability and report it.

## Handling of personal data

The healthcare dataset is **entirely synthetic**, generated deterministically
from a fixed PRNG seed by
`packages/lineagemedic/src/lineagemedic/data/seed_healthcare.py`. Patient
identifiers are sequential (`PT00001`, `PT00002`, …). No real patient data,
and no data derived from real patients, is present in this repository.

The planted quality defects — out-of-range ages, missing discharge dates — are
intentional and are the subject of the demo.

Do not point this tool at a real patient database without an independent review
of what its writeback would emit into your catalog.

## Trust boundaries

| Boundary | Position |
|----------|----------|
| Warehouse queries | Read-only. The workflow never writes to the warehouse. |
| Catalog reads | Unrestricted; safe and idempotent. |
| Catalog writes | Gated behind explicit human approval at three layers. |
| LLM narrator | Presentational only. Cannot influence severity, evidence, root cause, or the plan. |
| CORS | Restricted to configured origins via `LINEAGEMEDIC_CORS_ORIGINS`; methods limited to GET and POST. |

The approval gate is the security-relevant boundary in this system, because a
writeback mutates a catalog other teams depend on. It is enforced in the Safety
agent, in the Writeback agent, and at the HTTP endpoint, and each layer is
independently tested.

## Supported versions

This is a hackathon project under active development. Security fixes are
applied to the default branch only.
