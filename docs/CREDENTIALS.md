# Credentials and environment setup

**No credentials are necessary for the current synthetic research baseline, tests, API, or CI.**

| When | Secret/credential | Action |
|---|---|---|
| OpenAQ real-data pilot | `OPENAQ_API_KEY` | Create your key through the official OpenAQ platform. Store as a local environment variable or GitHub Actions secret when needed. |
| Database deployment | `DATABASE_URL` (future) | Provision managed PostgreSQL and store the connection URL in deployment secret management. Not implemented yet. |
| Deployment | Hosting account authorization (future) | Only needed when a specific hosting platform is selected. Not yet required. |
| Weather source | Provider-specific key if chosen (future) | Review source terms/quotas before adding any API credentials. |

Do **not** paste API keys, tokens, passwords, or connection strings into this chat, issues, README, commits, or screenshots. Never embed credentials in frontend code. `.env` is gitignored; `.env.example` is intentionally blank. Rotate any key that has been exposed.
