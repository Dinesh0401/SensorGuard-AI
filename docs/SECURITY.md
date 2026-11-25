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
| `POST /api/v1/admin/ingest` | Requires `X-Admin-Key: {ADMIN_API_KEY}` header; fails closed (`503`) if env var unset |
| `POST /api/v1/admin/evaluate` | Same as above |
| All `GET` endpoints | Public (read-only archived research data) |

Admin key validation uses constant-time `secrets.compare_digest` in `src/sensorguard/api.py` (`verify_admin_key`).

---

## 3. Input Validation

- All request bodies validated via Pydantic v2 schemas (`src/sensorguard/schemas.py`) with explicit bounds (`AdminIngestRequest.days` bounded `1..90`, `AdminEvaluateRequest.nominal_coverage` bounded `0.50..0.99`)
- Station IDs typed as integers (`404` if absent) and `bbox` coordinates strictly validated (`-180 <= min_lon < max_lon <= 180`, `-90 <= min_lat < max_lat <= 90`)
- Pagination limits clamped (`observations`: `1..1000`, `forecasts`: `1..168`)
- All DB access via SQLAlchemy 2.0 ORM parameterised queries

---

## 4. Data Provenance and Integrity

- Raw API responses stored with SHA-256 hash (`raw_ingest_batches.response_hash`)
- Model artefact verified via constant-time SHA-256 check against `manifest.json` (`verify_model_sha256` / `manifest.verify_file`); HTTP `503` if missing or invalid
- Dataset split hash (`33805c42097fc86e`) committed alongside benchmark results for reproducibility audit
- Append-only versioned observation storage via `(sensor_id, observed_at, version)`

---

## 5. Network Security & Container Hardening

- CORS origins explicitly configured via `CORS_ORIGINS` (`http://localhost:3000,http://127.0.0.1:3000` by default)
- In `docker-compose.yml`: PostgreSQL has **no host port exposure** (internal network only), requires non-default `POSTGRES_PASSWORD` and `ADMIN_API_KEY`, and binds FastAPI/frontend ports strictly to loopback `127.0.0.1`
- TLS/HTTPS termination via reverse proxy is required before any public exposure

---

## 6. Rate Limiting

- OpenAQ API: token-bucket rate limiter in `providers.py` (`RateLimiter`)
- FastAPI admin endpoints: built-in in-memory sliding-window rate limiter (`30` requests/min returning `HTTP 429`) and `asyncio.Lock()` concurrency mutex
- Public `GET` endpoints: deploy behind reverse proxy (`nginx`/`Caddy`) rate limiting in production

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
