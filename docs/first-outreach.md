# First sales outreach

This tutorial takes a clean installation to one manually approved email. Keep automatic contact
disabled until your sender domain, consent policy, and draft quality have been validated.

## 1. Verify prerequisites

Complete `make setup`, configure an AI endpoint, and add at least one contact provider. The shortest
working path uses Prospeo for discovery/contact resolution and Resend for delivery:

```dotenv
PROSPEO_API_KEY=replace-with-your-key
SALES_RESEND_API_KEY=replace-with-your-key
SALES_RESEND_DOMAIN=your-verified-domain.example
SALES_FROM_NAME=Your Company
SALES_FROM_EMAIL=sales@your-verified-domain.example
SALES_REPLY_TO_EMAIL=sales@your-verified-domain.example
SALES_AUTO_CONTACT_ENABLED=false
```

Optional `PDL_API_KEY` or `CE_API_KEY` adds company context. Run:

```bash
make doctor
make dev
```

Open `/operations/sales/discovery`. The Sales Doctor in the UI should show the features configured
by these values. It is normal for unused providers to remain disabled.

## 2. Discover companies

Enter an industry and location, choose a conservative result limit, and start research. Company
Core stores the returned records, deduplicates them, and separates contact enrichment from search.
Review provider warnings instead of immediately rerunning a successful search.

To protect credits:

- Start with five results per provider.
- Prefer different companies rather than several people from one company.
- Do not force a refresh when a saved result is usable.
- Resolve contact only for companies that match your customer profile.

You can also add a known company domain manually. A domain may be written as `example.com`; a URL
scheme is not required.

## 3. Resolve the contact and company

Open a lead card and run **Resolve contact**. This is the first action likely to consume a contact
credit. A successful or partial result is cached. Use force only when the saved record is stale or
incorrect.

Run **Resolve company** once when the draft needs more context. CompanyEnrich is preferred when
`CE_API_KEY` is configured; PDL is the fallback. Raw provider data and the normalized company
summary are saved, so draft generation does not need to call enrichment repeatedly.

Do not send when the address is missing, clearly invalid, suppressed, or outside your lawful
outreach policy.

## 4. Generate and edit the draft

Open `/operations/sales/email`, select the saved lead, and generate a draft. The agent uses the
saved contact and company profile. Review:

- Recipient and company identity.
- Every factual statement and product claim.
- Subject, greeting, CTA, and meeting URL.
- Consent, suppression, and jurisdiction requirements.

Edit the subject and body directly in the draft card, then save. Generating another draft is not a
substitute for reviewing the saved data.

## 5. Approve and send

Approval and sending are separate actions. Approve only the final saved version, then select
**Send**. Company Core sends through Resend and records the provider message ID. The lead moves to
`contacted`; delivery and reply webhook events appear on the same lead when configured.

The complete lifecycle is:

```text
new -> enriched -> qualified -> draft_ready -> approved -> contacted -> replied
                                                                  -> scheduled
```

Use **Suppress** for opt-outs, bad addresses, or contacts that must not receive further outreach.
Meetings can be marked manually with their scheduled time from the lead card.

## 6. Test before a real recipient

First send to an address you control. Confirm sender display name, HTML logo, plain-text fallback,
reply-to behavior, meeting link, delivery event, and suppression workflow before contacting a lead.
