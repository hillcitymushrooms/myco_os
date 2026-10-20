# Security policy and known limitations

This is an open-source early-stage product, not a security-certified public SaaS. Do not collect unrelated customers' operational or financial data until the deployment has been reviewed and privacy practices are in place.

Authentication uses Argon2id hashing and HttpOnly SameSite session cookies; state-changing authenticated requests use session-scoped CSRF tokens. Password reset and verification links are one-time and stored as hashes; reset revokes active sessions. Rate limiting runs in shared Redis in production. Farm records require authenticated tenant membership and role authorization.

Before public launch: validate reverse proxy IP behavior; introduce Alembic migrations; implement production monitoring, abuse reporting, and email bounce/rate monitoring; configure and test encrypted backups/restores; consider MFA, RLS defense in depth, private cloud logging, endpoint penetration testing, and privacy/legal review. Recovery tokens in development are logged to the local developer terminal. All secret URLs should be handled as credentials. Rotate them if disclosed.

Report vulnerabilities privately to the project maintainer rather than opening a public issue with exploit details.
