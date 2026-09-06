# Lead intake

External forms submit JSON to:

```text
POST /integrations/leads/website
```

Authenticate with `X-Company-Core-Lead-Secret: <SALES_INTAKE_SECRET>`. Keep this secret server-side;
never expose it in browser JavaScript. A frontend should post to its own serverless function or
backend, which validates the form and forwards the request.

## Payload

```json
{
  "full_name": "Alex Doe",
  "email": "alex@example.com",
  "company": "Example Logistics",
  "company_website": "example.com",
  "job_title": "Operations Manager",
  "country": "Rwanda",
  "message": "Improve shipment visibility",
  "consent": true,
  "source": "website",
  "source_detail": "signup_form",
  "utm_source": "instagram",
  "utm_medium": "social",
  "utm_campaign": "operations_visibility",
  "campaign_id": "optional-campaign-id",
  "post_id": "optional-post-id"
}
```

`company_website` is optional and accepts either `example.com` or a full URL. Attribution fields
are optional but should be preserved to compare campaigns and posts. Automatic email is disabled
by default; enable it only after reviewing sender configuration and consent requirements.
