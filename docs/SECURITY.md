# SensorGuard AI — Security Policy

**Version:** 1.0  
**Date:** 2026-11-25

---

## 1. Secret Management

| Rule | Implementation |
|---|---|
| No secrets in version control | `.env` is git-ignored; `.env.example` has only placeholder values |
| API keys server-side only | `OPENAQ_API_KEY` and `ADMIN_API_KEY` read from env at startup; never returned in API responses |
| No keys in frontend | `NEXT_PUBLIC_*` variables are public by design; never put secrets there |
| No keys in logs | Provider client strips `X-API-Key` header from log output |

---

## 2. Authentication and Authorisation

| Endpoint | Protection |
|---|---|
| `POST /api/v1/ingest` | Requires `X-Admin-Key: {ADMIN_API_KEY}` header |
| `POST /api/v1/admin/*` | Same as above |
| All `GET` endpoints | Public (read-only research data) |

Admin key validation: constant-time comparison via `hmac.compare_digest` to prevent timing attacks.

---

## 3. Input Validation

- All request bodies validated via Pydantic v2 schemas with explicit field constraints
- Station ID and sensor ID path parameters validated as integers
- Date range parameters validated: `from_date < to_date`, max window 90 days
- No arbitrary query execution; all DB access via SQLAlchemy ORM with parameterised queries

---

## 4. Data Provenance and Integrity

- Raw API responses stored with SHA-256 hash (`raw_ingest_batches.response_hash`)
- Model artefact verified against `EXPECTED_MODEL_SHA256` on API startup; HTTP 503 if mismatch
- Dataset split hash (`33805c42097fc86e`) committed alongside benchmark results for reproducibility audit
- Append-only observation store; no historical record deletion

---

## 5. Network Security

- CORS origins explicitly configured via `CORS_ORIGINS` environment variable
- Default CORS allows only `http://localhost:3000` and `http://127.0.0.1:3000`
- For production: set `CORS_ORIGINS` to only the actual frontend origin
- TLS/HTTPS required for any public deployment (not enforced locally)

---

## 6. Rate Limiting

- OpenAQ API: token-bucket rate limiter in `providers.py` (`RateLimiter`)
- FastAPI: no built-in rate limiting currently; production deployments should add middleware or a reverse proxy (nginx/Caddy) with rate limiting

---

## 7. Provider Attribution and Quotas

| Provider | Terms | Quota |
|---|---|---|
| UK AURN via OpenAQ | Open Government Licence v3.0; attribution required | Free tier API; check current rate limits at api.openaq.org |
| Open-Meteo (optional) | CC BY 4.0; attribution required | Non-commercial free tier |

Do not exceed provider quotas. The scheduler worker is configured for one ingestion pass per hour per station.

---

## 8. Privacy

- No user personal information is collected or stored
- No cookies, session tokens, or user accounts
- Dashboard is fully read-only for public users
- Admin endpoints require a bearer token but do not log any personal data

---

## 9. Dependency Security

- Python dependencies pinned in `pyproject.toml` with explicit version ranges
- Run `pip audit` or `safety check` before any production deployment
- Node.js dependencies: `npm audit` as part of frontend CI
- Known low-severity warning: `httpx` with starlette.testclient (test-only; use `httpx2` when upgrading)

---

## 10. Incident Response

If a secret is accidentally committed:
1. Immediately revoke the exposed key with the provider
2. Run `git filter-repo` or `BFG Repo Cleaner` to remove from history
3. Force-push the cleaned branch (requires repository owner approval)
4. Rotate all related credentials
5. Document the incident in a private post-mortem

For production security issues: contact the repository owner via GitHub private security advisory.
