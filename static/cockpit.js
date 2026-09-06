
(function () {
  const autosize = (el) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 280) + "px";
  };

  const textarea = document.querySelector('textarea[name="message"]');
  if (textarea) {
    autosize(textarea);
    textarea.addEventListener("input", () => autosize(textarea));
  }

  document.querySelectorAll("[data-scroll]").forEach((el) => {
    el.addEventListener("click", (event) => {
      const id = el.getAttribute("data-scroll");
      const target = document.getElementById(id);
      if (target) {
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  const now = document.querySelector("[data-clock]");
  if (now) {
    const tick = () => {
      now.textContent = new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    };
    tick();
    setInterval(tick, 30000);
  }
})();


document
  .querySelectorAll(".quick-command")
  .forEach((button) => {
    button.addEventListener("click", () => {
      const textarea = document.querySelector('textarea[name="message"]');
      if (!textarea) return;

      textarea.value = button.dataset.command || "";
      textarea.focus();
      textarea.dispatchEvent(new Event("input"));
    });
  });


(function campaignReview() {
  const list = document.getElementById("campaign-list");
  const count = document.getElementById("campaign-count");
  if (!list || !count) return;

  const terminal = new Set(["variants_ready", "selected", "drafted", "failed", "needs_campaign_review"]);
  let lastSignature = "";
  let refreshTimer = null;

  const el = (name, className, text) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  async function action(url, confirmation, options = {}) {
    if (confirmation && !window.confirm(confirmation)) return;
    const response = await fetch(url, { method: "POST", ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    await refresh();
  }

  function variantCard(campaign, variant) {
    const card = el("article", "variant-card");
    const top = el("div", "variant-top");
    top.append(el("strong", "", variant.voice), el("span", `status-pill status-${variant.status}`, variant.status));
    card.append(top);

    if (variant.status === "ready") {
      const video = document.createElement("video");
      video.controls = true;
      video.preload = "metadata";
      video.playsInline = true;
      video.src = variant.video_url;
      card.append(video);
      const meta = el("div", "variant-meta", `${variant.platform.toUpperCase()} · ${Number(variant.duration_seconds || 0).toFixed(1)}s · ${Number(variant.speed).toFixed(2)}×`);
      card.append(meta);
      const selected = campaign.selected_variant_id === variant.id;
      const button = el("button", selected ? "mini-action selected" : "mini-action", selected ? "SELECTED" : "SELECT VARIANT");
      button.disabled = selected || campaign.status === "drafted" || campaign.status === "drafting";
      button.addEventListener("click", () => action(
        `/company/marketing/campaigns/${campaign.id}/variants/${variant.id}/select`,
        "Select this render as the campaign video? This records the founder review decision."
      ).catch((error) => window.alert(error.message)));
      card.append(button);
    } else if (variant.error) {
      card.append(el("div", "variant-error", variant.error));
    } else {
      card.append(el("div", "variant-loading", "Rendering voice, media, and captions…"));
    }
    return card;
  }

  function readable(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.join("\n");
    return JSON.stringify(value, null, 2);
  }

  function artifactPanel(title, badge, content) {
    const panel = el("section", "artifact-panel");
    const head = el("div", "artifact-head");
    head.append(el("strong", "", title), el("span", "artifact-badge", badge));
    panel.append(head, content);
    return panel;
  }

  function scriptArtifacts(artifact) {
    const body = el("div", "artifact-body");
    if (artifact.title) body.append(el("h4", "", artifact.title));
    if (artifact.transcript) {
      const transcript = el("div", "artifact-transcript", artifact.transcript);
      body.append(transcript);
    }
    if (artifact.scenes && artifact.scenes.length) {
      const details = el("details", "artifact-details");
      const summary = el("summary", "", `${artifact.scenes.length} SCENES`);
      const scenes = el("div", "scene-list");
      artifact.scenes.forEach((scene) => {
        const item = el("div", "scene-item");
        const label = `SCENE ${scene.number}${scene.time ? ` · ${scene.time}` : ""}`;
        item.append(el("div", "scene-label", label));
        if (scene.headline) item.append(el("strong", "", scene.headline));
        if (scene.text) item.append(el("p", "", scene.text));
        if (scene.visual) item.append(el("div", "scene-visual", `Visual: ${scene.visual}`));
        scenes.append(item);
      });
      details.append(summary, scenes);
      body.append(details);
    }
    return artifactPanel("Campaign script", artifact.status || "CREATED", body);
  }

  function copyArtifacts(copy) {
    const body = el("div", "artifact-body copy-grid");
    Object.entries(copy).forEach(([platform, value]) => {
      const item = el("div", "copy-item");
      item.append(el("div", "scene-label", platform.replaceAll("_", " ")), el("p", "", readable(value)));
      body.append(item);
    });
    return artifactPanel("Platform copy", "READY", body);
  }

  function mediaArtifacts(artifact) {
    const body = el("div", "artifact-body");
    const summary = artifact.assets_acquired
      ? `${artifact.asset_count || artifact.scene_count || 0} media assets acquired and ready for rendering.`
      : `${artifact.scene_count || 0} scenes searched. Media selection is being prepared.`;
    body.append(el("p", "", summary));
    if (artifact.scenes && artifact.scenes.length) {
      const details = el("details", "artifact-details");
      details.append(el("summary", "", "VIEW MEDIA PLAN"));
      const scenes = el("div", "scene-list");
      artifact.scenes.forEach((scene) => {
        const item = el("div", "scene-item");
        item.append(el("div", "scene-label", `SCENE ${scene.number}`));
        if (scene.headline) item.append(el("strong", "", scene.headline));
        if (scene.text) item.append(el("p", "", scene.text));
        if (scene.visual) item.append(el("div", "scene-visual", scene.visual));
        scenes.append(item);
      });
      details.append(scenes);
      body.append(details);
    }
    return artifactPanel("Media production", artifact.assets_acquired ? "ACQUIRED" : "SEARCH READY", body);
  }

  function eventTimeline(events) {
    const timeline = el("div", "event-timeline");
    (events || []).slice(-7).reverse().forEach((event) => {
      const row = el("div", "event-row");
      const time = new Date(event.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      row.append(el("time", "", time), el("span", "", event.event.replaceAll(".", " · ").replaceAll("_", " ")));
      timeline.append(row);
    });
    return timeline;
  }

  function campaignCard(campaign) {
    const card = el("article", "campaign-card");
    const header = el("div", "campaign-header");
    const title = el("div", "");
    title.append(el("div", "kicker", campaign.id), el("h3", "", campaign.topic));
    header.append(title, el("span", `status-pill status-${campaign.status}`, campaign.status.replaceAll("_", " ")));
    card.append(header);

    const track = el("div", "stage-track");
    ["Growth brief", "G1 campaign", "Media search", "Variant review", "G3 drafts"].forEach((label, index) => {
      const step = el("span", "stage-step", label);
      const order = {
        growth_intake: 0, g1_queued: 1, g1_campaign: 1, g1_review: 1, media_queued: 2, media_search: 2,
        media_acquisition: 2, variant_rendering: 3, founder_variant_review: 3,
        founder_draft_approval: 3, g3_queued: 4, g3_draft_creation: 4, buffer_review: 4,
      }[campaign.current_stage] ?? 0;
      if (index <= order) step.classList.add("active");
      track.append(step);
    });
    card.append(track);

    const progress = campaign.progress || { percent: 0, label: campaign.current_stage };
    const progressWrap = el("div", "live-progress");
    const progressLabel = el("div", "progress-label");
    progressLabel.append(el("span", "", progress.label), el("strong", "", `${progress.percent}%`));
    const progressRail = el("div", "progress-rail");
    const progressFill = el("div", "progress-fill");
    progressFill.style.width = `${Math.max(0, Math.min(100, progress.percent))}%`;
    progressRail.append(progressFill);
    progressWrap.append(progressLabel, progressRail);
    card.append(progressWrap);

    const summary = el("div", "campaign-meta", `${campaign.objective.toUpperCase()} · ${campaign.buyer} · ${campaign.video_platform.toUpperCase()} · ${(campaign.social_platforms || []).join(" + ")}`);
    card.append(summary);
    if (campaign.error) card.append(el("div", "campaign-error", campaign.error));

    if (campaign.status === "needs_campaign_review") {
      const reviewEvent = [...(campaign.events || [])].reverse().find((event) => event.event === "campaign.needs_review");
      const warnings = reviewEvent && reviewEvent.payload && reviewEvent.payload.quality
        ? reviewEvent.payload.quality.warnings || []
        : [];
      const notice = el("div", "review-notice");
      notice.append(el("strong", "", "FOUNDER DECISION REQUIRED"));
      notice.append(el("p", "", warnings.length ? warnings.join(" · ") : "Review the saved script before media production."));
      card.append(notice);
    }

    const artifacts = campaign.artifacts || {};
    if (artifacts.script || artifacts.media) {
      const artifactGrid = el("div", "artifact-grid");
      if (artifacts.script) artifactGrid.append(scriptArtifacts(artifacts.script));
      if (artifacts.media) artifactGrid.append(mediaArtifacts(artifacts.media));
      card.append(artifactGrid);
      if (artifacts.script && Object.keys(artifacts.script.platform_copy || {}).length) {
        card.append(copyArtifacts(artifacts.script.platform_copy));
      }
    }

    if (campaign.variants && campaign.variants.length) {
      const variants = el("div", "variant-grid");
      campaign.variants.forEach((variant) => variants.append(variantCard(campaign, variant)));
      card.append(variants);
    } else if (!terminal.has(campaign.status)) {
      card.append(el("div", "campaign-progress", `Working: ${campaign.current_stage.replaceAll("_", " ")}…`));
    }

    const actions = el("div", "row-actions");
    if (campaign.status === "needs_campaign_review") {
      const approve = el("button", "mini-action approve", "APPROVE SCRIPT & CONTINUE");
      approve.addEventListener("click", () => action(
        `/company/marketing/campaigns/${campaign.id}/approve-script`,
        "Approve this exact script and continue directly to media search? The decision will be recorded."
      ).catch((error) => window.alert(error.message)));
      actions.append(approve);

      const regenerate = el("button", "mini-action secondary", "REGENERATE WITH NEW ANGLE");
      regenerate.addEventListener("click", () => {
        const direction = window.prompt(
          "Describe the new creative direction. Be specific enough to make this version materially different."
        );
        if (!direction) return;
        action(
          `/company/marketing/campaigns/${campaign.id}/regenerate-script`,
          "Regenerate G1 using this new direction? Existing generated files remain in the campaign folder for audit.",
          {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ direction }),
          }
        ).catch((error) => window.alert(error.message));
      });
      actions.append(regenerate);
    }
    if (campaign.status === "selected") {
      const draft = el("button", "mini-action", "CREATE BUFFER DRAFTS");
      draft.addEventListener("click", () => action(
        `/company/marketing/campaigns/${campaign.id}/drafts`,
        "Create draft-only Instagram and X posts in Buffer from the selected video? Nothing will be published or scheduled."
      ).catch((error) => window.alert(error.message)));
      actions.append(draft);
    }
    if (campaign.status === "failed") {
      const retryLabel = campaign.g3_status === "failed" ? "RETRY DRAFT CREATION" : "RETRY PIPELINE";
      const retry = el("button", "mini-action danger", retryLabel);
      retry.addEventListener("click", () => action(
        `/company/marketing/campaigns/${campaign.id}/retry`,
        campaign.g3_status === "failed"
          ? "Retry draft-only creation with the already selected video?"
          : "Retry from the failed stage? Completed upstream artifacts will be reused."
      ).catch((error) => window.alert(error.message)));
      actions.append(retry);
    }
    if (campaign.status === "drafted") {
      actions.append(el("div", "draft-confirmed", "✓ Drafts created. Final review and publishing remain in Buffer."));
    }
    card.append(actions);
    if (campaign.events && campaign.events.length) {
      const details = el("details", "event-details");
      details.append(el("summary", "", "LIVE ACTIVITY"), eventTimeline(campaign.events));
      card.append(details);
    }
    return card;
  }

  async function refresh(force = false) {
    try {
      const response = await fetch("/company/marketing/campaigns?limit=12", { cache: "no-store" });
      if (!response.ok) throw new Error(`Campaign sync failed (${response.status})`);
      const body = await response.json();
      const campaigns = body.campaigns || [];
      count.textContent = `${campaigns.length} CAMPAIGN${campaigns.length === 1 ? "" : "S"}`;
      const signature = JSON.stringify(campaigns);
      if (force || signature !== lastSignature) {
        lastSignature = signature;
        list.replaceChildren();
        if (!campaigns.length) {
          list.append(el("div", "muted", "No campaigns yet. Use the New Campaign command above."));
        } else {
          campaigns.forEach((campaign) => list.append(campaignCard(campaign)));
        }
      }
      const hasActive = campaigns.some((campaign) => !terminal.has(campaign.status));
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(refresh, hasActive ? 2000 : 10000);
    } catch (error) {
      list.replaceChildren(el("div", "campaign-error", error.message));
      count.textContent = "OFFLINE";
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(refresh, 10000);
    }
  }

  refresh();
})();


(function salesReview() {
  const list = document.getElementById("sales-list");
  const count = document.getElementById("sales-count");
  const health = document.getElementById("sales-health");
  const tokenInput = document.getElementById("sales-action-token");
  const saveToken = document.getElementById("sales-save-token");
  const syncInbox = document.getElementById("sales-sync-inbox");
  const prospectDomain = document.getElementById("sales-prospect-domain");
  if (!list || !count || !health || !tokenInput || !saveToken || !syncInbox || !prospectDomain) return;

  const tokenKey = "company-sales-action-token";
  let refreshTimer = null;
  let lastSignature = "";

  const el = (name, className, text) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function tokenValue() {
    return tokenInput.value.trim();
  }

  function ensureActionToken() {
    const value = tokenValue();
    if (value) return value;
    window.alert("Paste SALES_ACTION_TOKEN to unlock founder actions in the sales panel.");
    tokenInput.focus();
    return null;
  }

  function rememberToken() {
    const value = tokenValue();
    if (!value) {
      window.alert("Enter SALES_ACTION_TOKEN first.");
      tokenInput.focus();
      return;
    }
    window.sessionStorage.setItem(tokenKey, value);
    window.alert("Sales action token saved for this browser session.");
  }

  async function action(url, confirmation, options = {}) {
    const token = ensureActionToken();
    if (!token) return;
    if (confirmation && !window.confirm(confirmation)) return;
    const headers = new Headers(options.headers || {});
    headers.set("X-Founder-Action-Token", token);
    const response = await fetch(url, { method: "POST", ...options, headers });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    await refresh(true);
  }

  function stageTone(stage) {
    if (stage === "suppressed" || stage === "lost") return "danger";
    if (stage === "won" || stage === "replied") return "approve";
    if (stage === "approved" || stage === "qualified" || stage === "contacted") return "secondary";
    return "";
  }

  function renderHealthCard(doctor, leads) {
    const wrap = el("div", "sales-health-grid");
    const total = leads.summary?.total || 0;
    const byStage = leads.summary?.by_stage || {};
    const checks = doctor.checks || {};

    const cards = [
      ["Pipeline", `${total} leads · ${byStage.replied || 0} replied · ${byStage.approved || 0} approved`],
      ["Lead intake", checks.lead_intake ? "Configured" : "Missing SALES_INTAKE_SECRET or inbox config"],
      ["Outbound email", checks.outbound_email ? "Ready" : "Missing SALES_RESEND_API_KEY or SALES_FROM_EMAIL"],
      ["Inbound replies", checks.inbound_replies ? "Ready" : "Missing SALES_EMAIL_WEBHOOK_SECRET or SALES_REPLY_TO_EMAIL"],
    ];

    cards.forEach(([label, value]) => {
      const card = el("div", "sales-health-card");
      card.append(el("div", "sales-health-label", label), el("strong", "", value));
      wrap.append(card);
    });

    health.replaceChildren(wrap);
  }

  function latestDraft(lead) {
    return (lead.drafts || [])[0] || null;
  }

  function leadCard(lead) {
    const card = el("article", "lead-card");
    const header = el("div", "lead-header");
    const title = el("div", "");
    const name = lead.full_name || lead.company || lead.email || lead.id;
    title.append(el("div", "kicker", lead.id), el("h3", "", name));
    const stage = el("span", `status-pill status-${lead.stage}`, lead.stage.replaceAll("_", " "));
    header.append(title, stage);
    card.append(header);

    const meta = el("div", "lead-meta");
    [
      `score ${lead.lead_score || 0}`,
      lead.company || "no company",
      lead.job_title || "no title",
      lead.email || "no email",
      lead.source || "manual",
    ].forEach((item) => meta.append(el("span", "chip", item)));
    card.append(meta);

    if (lead.message) {
      card.append(el("p", "lead-message", lead.message));
    }

    const latest = lead.latest_interaction;
    if (latest) {
      const interaction = el("div", "lead-latest");
      interaction.append(
        el("div", "sales-health-label", `Latest ${latest.direction} ${latest.kind}`),
        el("div", "", latest.subject || "No subject"),
      );
      if (latest.body) interaction.append(el("p", "lead-message compact", latest.body));
      card.append(interaction);
    }

    const draft = latestDraft(lead);
    if (draft) {
      const draftBox = el("div", "lead-draft");
      draftBox.append(
        el("div", "sales-health-label", `Draft ${draft.status}`),
        el("strong", "", draft.subject || "Draft"),
        el("pre", "lead-draft-body", draft.body || "")
      );
      card.append(draftBox);
    }

    const actions = el("div", "row-actions");
    if (lead.stage !== "suppressed") {
      const enrich = el("button", "mini-action secondary", "ENRICH");
      enrich.addEventListener("click", () => action(
        `/company/sales/leads/${lead.id}/enrich`,
        "Enrich this lead with Hunter verification and update the score?"
      ).catch((error) => window.alert(error.message)));
      actions.append(enrich);
    }

    if (lead.email && lead.stage !== "suppressed") {
      const draftButton = el("button", "mini-action", draft ? "REDRAFT" : "CREATE DRAFT");
      draftButton.addEventListener("click", () => action(
        `/company/sales/leads/${lead.id}/draft`,
        "Generate an outreach draft for this lead?"
      ).catch((error) => window.alert(error.message)));
      actions.append(draftButton);
    }

    if (draft && draft.status === "draft") {
      const approve = el("button", "mini-action approve", "APPROVE DRAFT");
      approve.addEventListener("click", () => action(
        `/company/sales/drafts/${draft.id}/approve`,
        "Approve this draft for sending?"
      ).catch((error) => window.alert(error.message)));
      actions.append(approve);
    }

    if (draft && draft.status === "approved") {
      const send = el("button", "mini-action approve", "SEND EMAIL");
      send.addEventListener("click", () => action(
        `/company/sales/drafts/${draft.id}/send`,
        "Send this approved draft now via Resend?"
      ).catch((error) => window.alert(error.message)));
      actions.append(send);
    }

    if (lead.stage !== "suppressed") {
      const suppress = el("button", "mini-action danger", "SUPPRESS");
      suppress.addEventListener("click", () => {
        const reason = window.prompt("Suppression reason", "founder_request");
        if (!reason) return;
        action(
          `/company/sales/leads/${lead.id}/suppress?reason=${encodeURIComponent(reason)}`,
          "Suppress this lead and block future outreach?"
        ).catch((error) => window.alert(error.message));
      });
      actions.append(suppress);
    } else {
      actions.append(el("span", `mini-action ${stageTone(lead.stage)}`.trim(), `SUPPRESSED: ${lead.suppression_reason || "n/a"}`));
    }
    card.append(actions);
    return card;
  }

  async function refresh(force = false) {
    try {
      const [doctorResponse, leadsResponse] = await Promise.all([
        fetch("/company/sales/doctor", { cache: "no-store" }),
        fetch("/company/sales/leads?limit=24", { cache: "no-store" }),
      ]);
      if (!doctorResponse.ok) throw new Error(`Sales doctor failed (${doctorResponse.status})`);
      if (!leadsResponse.ok) throw new Error(`Lead sync failed (${leadsResponse.status})`);
      const doctor = await doctorResponse.json();
      const leads = await leadsResponse.json();
      const items = leads.leads || [];
      count.textContent = `${items.length} LEAD${items.length === 1 ? "" : "S"}`;
      renderHealthCard(doctor, leads);
      const signature = JSON.stringify(items);
      if (force || signature !== lastSignature) {
        lastSignature = signature;
        list.replaceChildren();
        if (!items.length) {
          list.append(el("div", "muted", "No leads yet. Sync website forms or submit a test intake request."));
        } else {
          items.forEach((lead) => list.append(leadCard(lead)));
        }
      }
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(refresh, 10000);
    } catch (error) {
      health.replaceChildren(el("div", "campaign-error", error.message));
      list.replaceChildren(el("div", "campaign-error", error.message));
      count.textContent = "OFFLINE";
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(refresh, 10000);
    }
  }

  const savedToken = window.sessionStorage.getItem(tokenKey);
  if (savedToken) tokenInput.value = savedToken;

  saveToken.addEventListener("click", rememberToken);
  tokenInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      rememberToken();
    }
  });

  syncInbox.addEventListener("click", () => action(
    "/company/sales/inbound/sync",
    "Pull pending website leads from SALES_INBOX_URL now?"
  ).catch((error) => window.alert(error.message)));

  prospectDomain.addEventListener("click", () => {
    const domain = window.prompt("Company domain to prospect", "");
    if (!domain) return;
    const limitRaw = window.prompt("How many contacts to import? (1-10)", "5");
    const limit = Number(limitRaw || 5);
    action(
      "/company/sales/prospect/domain",
      `Import up to ${Number.isFinite(limit) ? limit : 5} contacts from ${domain}?`,
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, limit: Math.max(1, Math.min(10, Number.isFinite(limit) ? limit : 5)) }),
      }
    ).catch((error) => window.alert(error.message));
  });

  refresh();
})();
