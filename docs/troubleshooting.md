# Troubleshooting

Start with:

```bash
make doctor
curl http://localhost:8787/health
curl http://localhost:8787/company/sales/doctor -u "$DASHBOARD_USER:$DASHBOARD_PASSWORD"
curl http://localhost:8787/company/marketing/doctor -u "$DASHBOARD_USER:$DASHBOARD_PASSWORD"
```

## Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `401` | Missing dashboard/action/webhook secret | Check the required header and matching `.env` value |
| `403` from provider | Plan restriction, blocked request, or unverified domain | Read the provider response; verify account access instead of retrying repeatedly |
| `409` | Action does not match the current lifecycle state | Complete the preceding review, approval, or selection step |
| `422` | Payload validation failed | Compare field names and types with `/docs` and `docs/lead-intake.md` |
| `502` | Upstream provider or model gateway failed | Read the nested method, URL, status, and body; test that provider directly |
| Cloudflare `524` | A synchronous operation exceeded proxy timeout | Confirm background workers are used and inspect origin logs |
| Empty search results | Filters are too narrow or provider has no match | Broaden industry/location once; do not repeatedly force the same lookup |
| Model connection failure | Wrong base URL, key, alias, or router is stopped | Query `/v1/models`; use real model IDs with Ollama |
| Media doctor unavailable | G1/G2/G3 environment was not installed | Run `make setup-engines` |
| Showcase/outro unavailable | Placeholder paths were not replaced | Point both variables to owned files that exist locally |
| FFmpeg error | FFmpeg is absent or source media is invalid | Run `ffmpeg -version` and validate the input file |
| Buffer draft failure | Missing channel ID or media URL is not public | Run the G3 doctor and open the R2 media URL without authentication |
| Resend domain error | Sender domain is not verified | Verify the domain and make `SALES_FROM_EMAIL` use that domain |

## Logs and retries

Run `make dev` in a terminal and preserve the complete exception, including upstream status and
response body. Retry only after changing the failing condition. Contact and company resolution are
cached; `force=true` deliberately bypasses that protection and may consume another credit.

For a failed campaign, correct missing configuration or media first, then use **Retry**. Campaigns
waiting for review must be approved or regenerated instead of retried.
