# First marketing campaign

This tutorial creates one reviewed campaign package and, optionally, a draft in Buffer. Company
Core does not publish directly from the generation step.

## 1. Configure the company source of truth

Replace the example content in:

```text
engines/g1/knowledge/brand_profile.json
engines/g1/knowledge/product_claims.json
engines/g1/knowledge/cta_registry.json
engines/g1/knowledge/icp_registry.json
engines/g1/knowledge/narrative_registry.json
```

Only add claims that you can support. G1 uses these files to validate generated copy.

Add your owned product media and update `.env`:

```dotenv
MARKETING_G2_SHOWCASE=engines/g2/assets/references/your-showcase.mp4
MARKETING_G2_OUTRO=engines/g2/assets/references/your-outro.png
PEXELS_API_KEY=replace-with-your-key
PIXABAY_API_KEY=replace-with-your-key
MARKETING_CTA_URL=https://forms.your-domain.example
```

The showcase can be an owned image or video. Use a portrait 1080x1920 end card for short-form
video. Run `make doctor` and confirm G1, G2, showcase, and outro are ready.

## 2. Create a campaign

Open `/operations/marketing/campaigns`. Choose an objective, provide a concrete brief, select social
platforms and a video platform, then choose TTS or real voice. The AI selects the buyer and topic
from your direction; a supplied transcript remains the narration source.

Campaign processing is asynchronous. Leave the page open or refresh the list rather than submitting
the same campaign repeatedly.

## 3. Review the script

When status becomes `needs_campaign_review`, inspect the buyer, narrative, claims, script, scenes,
and platform copy. Choose:

- **Approve script** to begin media search and rendering.
- **Regenerate** with a specific correction when the direction is wrong.

Do not approve unsupported statistics, invented customer outcomes, or incorrect product behavior.

## 4. Complete voice and media

TTS campaigns continue automatically. Real-voice campaigns pause for an audio upload. Supported
recording types include WAV, MP3, M4A, AAC, OGG, WebM, MP4, and MOV.

G2 searches configured stock providers and uses your owned showcase/outro for protected brand
scenes. Review licensing and visual relevance. A media failure can be retried after correcting the
reported provider, FFmpeg, timeout, or missing-asset issue.

## 5. Select and hand off

When variants are ready, preview and select one. Platform captions remain editable before the final
handoff. Creating drafts sends a validated, draft-only package to G3. G3 uploads public media to R2
and creates Buffer drafts; an operator still performs final approval in Buffer.

```text
brief -> G1 package -> script review -> media search -> G2 variants
      -> variant selection -> G3 draft -> Buffer approval -> publish
```

## 6. Track conversion

Keep the generated tracked form URL in the caption or profile link. The URL carries campaign and
post identifiers. Leads submitted through that form enter the sales queue with attribution, letting
you compare which post and campaign produced qualified contacts.

For an existing carousel or video, use the manual upload workflow under Marketing Assets. Upload
all files, finalize the post to generate its caption and tracked URL, then create the Buffer draft.
