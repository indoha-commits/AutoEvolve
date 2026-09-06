# Provider setup

All external providers are optional. Add credentials only for the workflow you use, restart Company
Core, and inspect `/company/sales/doctor` or `/company/marketing/doctor`.

| Provider | Purpose | Configuration | Credit-sensitive action |
| --- | --- | --- | --- |
| Prospeo | Company/person search and contact resolution | `PROSPEO_API_KEY` | Search and resolve contact |
| Lusha | Additional prospect search/contact source | `LUSHA_API_KEY`, optional `LUSHA_USER_AGENT` | Search and contact lookup |
| Hunter | Domain discovery, email lookup/verification | `HUNTER_API_KEY` | Lookup and verification |
| Apollo | Optional enrichment where the account permits its endpoints | `APOLLO_API_KEY` | Provider-dependent lookup |
| CompanyEnrich | Company profile by domain | `CE_API_KEY` | Company resolution |
| People Data Labs | Company profile fallback | `PDL_API_KEY` | Company resolution |
| Resend | Approved outbound email and event tracking | `SALES_RESEND_*` | Sending email |
| Pexels | Stock image/video search | `PEXELS_API_KEY` | Provider request |
| Pixabay | Stock media fallback | `PIXABAY_API_KEY` | Provider request |
| Buffer | Social draft creation | `BUFFER_API_KEY` and channel IDs | Draft creation |
| Cloudflare R2 | Public media hosting for Buffer | `R2_*` | Upload/storage |

## Sales providers

Provider keys are created in each provider's account dashboard. Put them only in `.env`. Company
Core's company-first workflow saves search results before contact resolution, caches resolved
contacts, and caches company profiles. Avoid `force=true` unless you intentionally want another
billable lookup.

You do not need every sales provider. A practical minimum is Prospeo plus one company enrichment
provider. Hunter and Lusha can be added as alternatives. Apollo features depend on which endpoints
your account plan permits; a configured key does not guarantee access to restricted endpoints.

## Resend

Verify your sending domain with Resend, then set:

```dotenv
SALES_RESEND_API_KEY=replace-with-your-key
SALES_RESEND_DOMAIN=your-verified-domain.example
SALES_FROM_NAME=Your Company
SALES_FROM_EMAIL=sales@your-verified-domain.example
SALES_REPLY_TO_EMAIL=sales@your-verified-domain.example
SALES_RESEND_WEBHOOK_SECRET=replace-with-your-webhook-secret
```

The `SALES_FROM_EMAIL` domain must match a domain authorized in your Resend account. Configure the
Resend webhook to send events to `/integrations/resend/sales` on your public Company Core URL. Keep
automatic contact disabled until a controlled test email succeeds.

## Media providers

Set `PEXELS_API_KEY` and/or `PIXABAY_API_KEY` in the root `.env`; G2 inherits the application
environment. Stock results still require operator review for relevance and licensing. Lordicon is
optional and uses `LORDICON_API_TOKEN`.

## Buffer and R2

Run `make init`, then edit the ignored file `engines/g3/config/g3.env`:

```dotenv
BUFFER_API_KEY=replace-with-your-key
BUFFER_ORGANIZATION_ID=replace-with-your-organization-id
BUFFER_INSTAGRAM_CHANNEL_ID=replace-with-your-channel-id
BUFFER_X_CHANNEL_ID=replace-with-your-channel-id
R2_ACCOUNT_ID=replace-with-your-account-id
R2_ACCESS_KEY_ID=replace-with-your-access-key
R2_SECRET_ACCESS_KEY=replace-with-your-secret-key
R2_BUCKET=company-core-social-media
R2_PUBLIC_BASE_URL=https://media.your-domain.example
```

The R2 public URL must use HTTPS and allow anonymous reads of published media. Validate before a
campaign:

```bash
engines/g3/.venv/bin/company-core-g3 account
engines/g3/.venv/bin/company-core-g3 channels
engines/g3/.venv/bin/company-core-g3 doctor --require instagram --require x
```
