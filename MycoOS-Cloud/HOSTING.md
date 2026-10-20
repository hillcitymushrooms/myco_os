# Hosting Myco OS Cloud

## Recommended path: Render + managed Postgres + Redis + Resend

Render can run this existing Python/FastAPI project with a web service, provides custom domains and TLS, offers managed PostgreSQL, and a Redis-compatible key-value service. You can use the Dockerfile in this repository. Host the front-end and API together, not as a static HTML upload.

1. Put the source code in your own private or public Git repository (do not commit `.env` or secrets).
2. Create a **paid** Render PostgreSQL database with backups and a Redis/Key Value instance.
3. Create a Render Web Service from the repo using Docker.
4. Configure `APP_ENV=production`, `PUBLIC_APP_URL=https://app.yourdomain.com`, `DATABASE_URL` (use the internal Postgres URL in psycopg format), `REDIS_URL`, `RESEND_API_KEY`, `MAIL_FROM`.
5. In Resend verify a sending domain and use a verified From address.
6. Point the domain DNS to your web service, confirm HTTPS, then test a real email-verified signup, password reset and invite accept from different devices.
7. Restore/test a database backup in an isolated staging environment.
8. Add Stripe environment variables and real prices only after paid plans are ready to sell; test webhooks in Stripe test mode.

Render's free PostgreSQL expires after 30 days and offers no backups. **Do not use its free database for customer farm records.** Render paid Postgres includes point-in-time recovery (available retention depends on plan).

### Alternative: Railway

Railway deploys Docker-based Python apps quickly. Use its Hobby/Pro plan plus PostgreSQL and Redis services, configure the same environment variables, enable backups and inspect monthly usage. Its Hobby plan starts at $5/month including $5 of usage credit, but compute/database/storage/egress can exceed the included amount. PostgreSQL and Redis are NOT somehow free just because the account is on Hobby.

### Alternative: Supabase Postgres + Render web service

Supabase Pro offers managed PostgreSQL with daily backups, but the current Myco OS code uses **its own FastAPI authentication and database models**, not Supabase Auth. Connecting Supabase as a Postgres provider does not automatically migrate authentication or add row-level security. You would still need the Render web service, Redis, Resend and backup verification.

## Typical ongoing services

| Component | What it does |
| --- | --- |
| Web service | Runs FastAPI and serves the Myco OS web app |
| PostgreSQL | Canonical farm, employee, harvest and sales data |
| Redis | Shared authentication abuse prevention |
| Resend | Verification, reset and recovery emails |
| Domain + HTTPS | Secure browser access |
| Stripe | Optional paid farm subscriptions |
| Backup storage | Independent offsite recovery |

Current vendor prices fluctuate. Check official plan/pricing pages before purchasing. As a rough starting budget, expect **paid hosting**, often a few tens of dollars per month across these components, not a genuinely free production service.

## Deployment warnings

- The included `compose.yaml` is **only for development** and contains placeholder passwords.
- Never expose a SQLite-backed development deployment as a public multi-user production service.
- Never deploy with `APP_ENV=development` or `PUBLIC_APP_URL=http://...` publicly.
- If a provider proxies traffic, confirm its trusted proxy forwarding configuration. Set `TRUSTED_PROXY_IPS` ONLY to actual proxy addresses/CIDRs (never `*`). IP-based rate limits must use the genuine client IP, not a shared proxy address or an attacker-supplied header.
- Backups must be restored and tested. The app's JSON export is not equivalent to a full database backup.
- Before inviting unrelated customers, complete a professional security and privacy review.

## References

- Render free plan and Postgres expiration: https://render.com/docs/free
- Render Postgres recovery: https://render.com/docs/postgresql-backups
- Railway pricing: https://docs.railway.com/pricing
- Supabase pricing: https://supabase.com/pricing
- Resend pricing: https://resend.com/pricing
