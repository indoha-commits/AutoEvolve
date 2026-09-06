# Production checklist

Complete this checklist before exposing Company Core or sending real outreach.

## Access and secrets

- Use long unique dashboard, action, intake, email webhook, and provider secrets.
- Keep `.env` readable only by the service account and confirm it is ignored by Git.
- Put the application behind HTTPS and an identity-aware proxy where possible.
- Do not expose operator actions directly to an untrusted frontend.
- Rate-limit public intake and webhook endpoints.

## Email and lead data

- Verify the Resend sender domain and test SPF, DKIM, DMARC, reply-to, and unsubscribe handling.
- Send first to an address you control and confirm HTML plus plain-text rendering.
- Keep `SALES_AUTO_CONTACT_ENABLED=false` until manual approval is proven reliable.
- Define lawful basis, consent, retention, deletion, and suppression procedures for your region.
- Never place real lead payloads in issues, fixtures, screenshots, or logs shared publicly.

## Models and claims

- Confirm every configured model ID exists and supports the required workload.
- Replace example company knowledge and review all approved product claims.
- Treat model output as a draft; retain human approval before email or publishing.
- Use only licensed stock, owned media, and authorized logos.

## Reliability

- Run `make check` and `make doctor` on the deployment host.
- Back up `.env`, `config/`, `data/`, `projects/`, and G3 configuration securely.
- Run one application process while using SQLite.
- Configure process restart, health monitoring, disk limits, and log rotation.
- Test restoration and webhook delivery before announcing the deployment.
