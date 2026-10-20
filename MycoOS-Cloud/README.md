# Myco OS Cloud v1.1

Copyright © 2026 Hill City Mushrooms · Open source under the MIT License.

Myco OS is a multi-farm mushroom grow tracker with team collaboration, cultivation and laboratory records, harvest-to-inventory traceability, customer orders, tasks and financial summaries. The software is open source; unlimited employee accounts and memberships are **free on all subscription tiers**. Pro adds the working performance CSV report; Growth currently includes that same report, with further premium features planned but **not yet delivered**.

**Status:** Tested local / cloud-ready starter project. Not deployed, audited or certified for public production use. Use staging and independent security review before letting unrelated commercial farms enroll.

## What's new in v1.1

- Unlimited team invitations and farm memberships on the Free, Pro and Growth plans.
- Owners can change employee roles or remove memberships; owners cannot remove themselves or demote their owner account.
- Individual users can create multiple farm workspaces, all tenant-isolated.
- Password recovery via single-use, 30-minute links. Resets revoke all existing sessions.
- Account verification via single-use, 24-hour links for production signups. Existing accounts are grandfathered in if upgraded from the starter.
- Password-change workflow that revokes other logged-in devices.
- Signup, login, invitations, recovery and account creation are rate limited. Development limits are local/in-memory; production requires shared Redis.
- HTTPS cookie protections, CSRF tokens, origin checks, security response headers, request-size limits, Argon2 password hashing, hashed login/recovery tokens and session expiry.
- Premium performance report CSV with farm-level authorization; free users continue to have standard analytics and JSON export.
- Updated onboarding instructions, clearer plan explanations and security controls.

## Try it on Windows

1. Install Python 3.11+ if needed.
2. Extract the ZIP, open the `MycoOS-Cloud` folder.
3. Double-click `start_windows.bat`. First launch downloads Python packages.
4. Visit <http://127.0.0.1:8000> and create a farm.
5. Your data saves to the local development SQLite database after successful changes.
6. Use Settings → Backups to export a JSON copy of farm records.

On macOS/Linux run `./start_mac_linux.sh`. Alternatively:

```bash
python -m venv .venv
# On Windows use .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### Password recovery when running locally

With `APP_ENV=development` and no configured `RESEND_API_KEY`, email recovery and verification messages print to the **Python terminal window**. To try it, click **Forgot password?**, submit your email and open the `?reset=...` link shown in the terminal. Links do not go to a real email inbox until Resend is configured. Never run development mode on the public internet.

### Upgrading your existing Myco OS Cloud starter

1. **Back up your database** (`myco-cloud.db` or managed Postgres) BEFORE replacing code.
2. Stop the running server.
3. Replace the app code with the files in this ZIP, preserving your database file, not overwriting it with a demo file.
4. Run the updated launcher. Existing users are allowed to sign in and the new `email_verified` flag is added if necessary. New local development accounts are automatically verified; public production signups require email verification.
5. Test team permissions, backup and restore in a staging copy first.

An additive database change is applied at startup, but **this is not a full migration framework**. For future public releases use Alembic migrations, deploy review and tested rollback plans.

## Billing and product tiers

- **Free:** all core growing, traceability, sales, team collaboration, employee invitations, farm memberships and JSON export. **No seat limit.**
- **Pro:** Free features plus downloadable per-species performance CSV report. Requires configured Stripe price and successful checkout.
- **Growth:** currently contains the same implemented functionality as Pro. Other integrations/automations are planned, not available yet. Do not sell Growth as containing unfinished features.

Plans apply to farms, not individual users. An employee may join multiple farms without paying for seats. There is no enforced farm-record usage quota in this version. All subscriptions and payment events require valid Stripe configuration.

## Running the dev Docker setup

```bash
docker compose up --build
```

This is a **development-only** Compose configuration with sample database credentials; do not expose it publicly.

## Production deployment checklist

1. Provision managed PostgreSQL, take an independently recoverable database backup, enable automated backups and test restoration.
2. Provision hosted **Redis** and set `REDIS_URL` so authentication throttling works across instances. The app refuses to start with `APP_ENV=production` without Redis.
3. Set `APP_ENV=production`, `PUBLIC_APP_URL=https://your-domain`, `DATABASE_URL`, `REDIS_URL`, `RESEND_API_KEY` and `MAIL_FROM` in the hosting platform's secrets dashboard. Do **not** check secrets into Git.
4. Verify a sending domain with Resend, enable HTTPS, and use a trusted reverse proxy configured to preserve the actual client IP rather than accepting arbitrary forwarded headers. Check that response cookies are Secure and HttpOnly.
5. Configure Stripe secret, webhook signature secret and price IDs only when ready to sell Pro. Billing webhooks are handled under `/api/billing/webhook`; subscribe to the supported Checkout/subscription events and verify events in Stripe sandbox before receiving live payments.
6. Restrict access to any internal databases, secrets and monitoring endpoints. Enable request/error logging without logging passwords, credentials or recovery tokens.
7. Run tests, establish abuse and account-recovery monitoring, verify daily backups, rehearse full restore in staging and conduct an independent security review.
8. Confirm privacy policy, data retention, user deletion/export rights and terms before selling the service publicly.

See **HOSTING.md** for platform comparisons and a suggested deployment path.

## Data integrity and portability

- All successful changes are saved on the server. This is not currently an offline-syncing app.
- Every farm's records are checked by authenticated farm membership and scoped roles.
- Optimistic revision checks prevent silent overwrites of generic records.
- Recording a harvest adds traceable fresh inventory; fulfilling an order deducts the referenced lot.
- JSON export provides portable records, but importing a cloud export is not a full database restore. Keep actual **PostgreSQL backups**.
- The built-in import of the old standalone HTML JSON is intentionally partial: it excludes legacy harvests, stock and sales to avoid accounting corruption.

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
node --check static/app.js
```

Tests cover cross-farm isolation, CSRF/role checks, inventory/order flows, free invitations, multi-farm creation, one-time password reset, session revocation, rate limits and security headers. E2E browser tests should be performed again on the target hosting environment; completing backend tests does **not** mean this is security audited.

## Security limitations to address before public launch

This app uses server-level tenant filters but not PostgreSQL row-level security. Production use warrants an independent audit for IDOR, CSRF, race conditions, billing correctness, email deliverability, recovery abuse, and deployment misconfiguration. There is no MFA, CAPTCHA, account locking, background backup scheduler, webhook reconciliation queue or event-driven push sync yet. Actual DB schema migrations and production logging/monitoring should be added before a public paid launch.

## License

MIT License · Copyright © 2026 Hill City Mushrooms. Everyone may copy, modify, share, redistribute, commercially host, and self-host the software provided they preserve the legally required copyright and MIT permission notice in copies or substantial portions. Editable source code cannot make attribution technically impossible to remove.
