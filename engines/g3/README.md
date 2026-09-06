# G3 Publishing Engine

G3 validates G2 handoffs, uploads media to S3-compatible storage such as Cloudflare R2, and creates
draft posts in Buffer. It does not bypass the operator approval workflow.

```bash
cp engines/g3/config/g3.env.example engines/g3/config/g3.env
engines/g3/.venv/bin/company-core-g3 doctor --require instagram --require x
```

Keep `g3.env` private. Configure your bucket, endpoint, public media URL, Buffer token, and channel
IDs before enabling publishing actions.
