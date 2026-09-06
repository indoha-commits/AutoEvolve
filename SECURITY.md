# Security Policy

## Supported versions

Security fixes are applied to the default branch and the latest tagged `0.x` release. Until `1.0`,
minor releases may contain configuration or data migrations; read `CHANGELOG.md` before upgrading.

## Reporting

Do not open a public issue for a suspected vulnerability. After the repository is published on GitHub, use its **Security > Report a vulnerability** private advisory form. Until then, report directly to the repository owner through a private channel.

Include the affected version, reproduction steps, impact, and any suggested mitigation. Do not include real credentials or customer data.

## Deployment baseline

- Generate strong `DASHBOARD_PASSWORD`, `SALES_ACTION_TOKEN`, `SALES_INTAKE_SECRET`, and webhook secrets.
- Put public deployments behind TLS and rate limiting.
- Keep operator routes private or behind an identity-aware proxy.
- Use a distinct secret for intake and provider webhooks.
- Verify webhook signatures against the raw request body.
- Restrict database and upload permissions to the service account.
- Rotate any credential that appears in logs, issues, commits, or screenshots.
- Back up and test restoration before upgrades.

## Data handling

Lead and email data may be personal data. Operators are responsible for lawful collection, consent, retention, deletion, suppression, and provider compliance in their jurisdiction. The software must not be presented as legal compliance automation.
