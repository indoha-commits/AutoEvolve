import PostalMime from "postal-mime";

export default {
  async email(message, env, ctx) {
    const raw = await new Response(message.raw).arrayBuffer();
    const parsed = await PostalMime.parse(raw);

    const referencesHeader = message.headers.get("references") || "";
    const references = referencesHeader
      .split(/\s+/)
      .map((value) => value.trim())
      .filter(Boolean);

    const payload = {
      from_email: message.from,
      to_email: message.to,
      subject: parsed.subject || "Reply",
      text: parsed.text || "",
      html: parsed.html || "",
      message_id: message.headers.get("message-id") || "",
      in_reply_to: message.headers.get("in-reply-to") || "",
      references,
      received_at: new Date().toISOString(),
      headers: Object.fromEntries(message.headers.entries()),
      source: "cloudflare_email_routing",
    };

    const response = await fetch(env.COMPANY_SALES_INGRESS_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Sales-Email-Webhook-Secret": env.SALES_EMAIL_WEBHOOK_SECRET,
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(
        `Company sales ingress failed: ${response.status} ${await response.text()}`
      );
    }
  },
};
