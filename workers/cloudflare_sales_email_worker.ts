import PostalMime from "postal-mime";

export interface Env {
  COMPANY_SALES_INGRESS_URL: string;
  SALES_EMAIL_WEBHOOK_SECRET: string;
}

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    const raw = await new Response(message.raw).arrayBuffer();
    const parsed = await PostalMime.parse(raw);
    const references = parsed.headers?.references;
    const payload = {
      from_email: message.from,
      to_email: message.to,
      subject: parsed.subject || "Reply",
      text: parsed.text || "",
      html: parsed.html || "",
      message_id: message.headers.get("message-id") || "",
      in_reply_to: message.headers.get("in-reply-to") || "",
      references: Array.isArray(references) ? references : (references ? String(references).split(/\s+/).filter(Boolean) : []),
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
      throw new Error(`Company sales ingress failed: ${response.status} ${await response.text()}`);
    }
  },
};
