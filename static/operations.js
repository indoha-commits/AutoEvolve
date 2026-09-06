(function () {
  const overviewUrl = "/company/operations/overview";
  const pageMode = document.body?.dataset?.page || "operations";
  const salesView = document.body?.dataset?.salesView || "combined";
  const focusedSalesLeadId = new URLSearchParams(window.location.search).get("lead") || "";
  const archiveKey = "company-operations-archive-v1";
  const terminalMarketing = new Set(["drafted", "failed"]);
  const terminalSales = new Set(["contacted", "replied", "won", "lost", "suppressed"]);
  const flash = document.getElementById("operations-flash");
  const operationsHealth = document.getElementById("operations-health");
  const projectChip = document.getElementById("operations-project-chip");
  const globalRefresh = document.getElementById("global-refresh");
  const tokenInput = document.getElementById("sales-action-token");
  const saveToken = document.getElementById("sales-save-token");
  const marketingList = document.getElementById("marketing-list");
  const marketingCount = document.getElementById("marketing-count");
  const salesList = document.getElementById("sales-list");
  const salesCount = document.getElementById("sales-count");
  const historyList = document.getElementById("history-list");
  const historyCount = document.getElementById("history-count");
  const historySearch = document.getElementById("history-search");
  const assetPackList = document.getElementById("asset-pack-list");
  const assetPackCount = document.getElementById("asset-pack-count");
  const manualPostList = document.getElementById("manual-post-list");
  const manualPostCount = document.getElementById("manual-post-count");
  const marketingForm = document.getElementById("campaign-launch-form");
  const leadForm = document.getElementById("lead-launch-form");
  const prospectForm = document.getElementById("prospect-launch-form");
  const researchForm = document.getElementById("research-launch-form");
  const assetPackForm = document.getElementById("asset-pack-form");
  const manualPostForm = document.getElementById("manual-post-form");
  const industryInput = researchForm?.querySelector("input[name=industry]");
  const locationInput = researchForm?.querySelector("input[name=location]");
  const tokenKey = "company-sales-action-token";
  const state = {
    archived: readArchive(),
    marketingFilter: "all",
    salesFilter: "all",
    salesSourceFilter: "all",
    historyFilter: pageMode === "sales" ? "sales" : pageMode === "marketing" ? "marketing" : "all",
    search: "",
    refreshTimer: null,
    overview: null,
  };

  const el = (name, className, text) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };


  function readArchive() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(archiveKey) || "{}");
      return {
        marketing: Array.isArray(parsed.marketing) ? parsed.marketing : [],
        sales: Array.isArray(parsed.sales) ? parsed.sales : [],
      };
    } catch {
      return { marketing: [], sales: [] };
    }
  }

  function writeArchive() {
    window.localStorage.setItem(archiveKey, JSON.stringify(state.archived));
  }

  function isArchived(kind, id) {
    return state.archived[kind]?.includes(id);
  }

  function archiveEntity(kind, id) {
    if (!state.archived[kind]) state.archived[kind] = [];
    if (!state.archived[kind].includes(id)) {
      state.archived[kind].push(id);
      writeArchive();
    }
  }

  function restoreEntity(kind, id) {
    if (!state.archived[kind]) return;
    state.archived[kind] = state.archived[kind].filter((item) => item !== id);
    writeArchive();
  }

  function visibleMarketing(campaigns) {
    return (campaigns || []).filter((campaign) => pageMode === "history"
      ? isArchived("marketing", campaign.id)
      : !isArchived("marketing", campaign.id));
  }

  function visibleSales(leads) {
    return (leads || []).filter((lead) => pageMode === "history"
      ? isArchived("sales", lead.id)
      : !isArchived("sales", lead.id));
  }

  function setFlash(message, tone = "muted") {
    if (!flash) return;
    flash.className = `operations-flash ${tone}`;
    flash.textContent = message;
  }

  function autosizeAll() {
    document.querySelectorAll("textarea").forEach((textarea) => {
      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
      textarea.addEventListener("input", () => {
        textarea.style.height = "auto";
        textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
      });
    });
  }

  function tickClock() {
    const clock = document.querySelector("[data-clock]");
    if (!clock) return;
    const update = () => {
      clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    };
    update();
    window.setInterval(update, 30000);
  }

  function tokenValue() {
    return (tokenInput?.value || "").trim();
  }

  function ensureToken() {
    const value = tokenValue();
    if (value) return value;
    window.alert("Paste SALES_ACTION_TOKEN to unlock direct sales actions.");
    tokenInput?.focus();
    return null;
  }

  function rememberToken() {
    const value = tokenValue();
    if (!value) {
      window.alert("Enter SALES_ACTION_TOKEN first.");
      tokenInput?.focus();
      return;
    }
    window.sessionStorage.setItem(tokenKey, value);
    setFlash("Sales action token saved for this browser session.", "success-copy");
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }

  async function salesAction(url, confirmation, options = {}) {
    const token = ensureToken();
    if (!token) return;
    if (confirmation && !window.confirm(confirmation)) return;
    const headers = new Headers(options.headers || {});
    headers.set("X-Founder-Action-Token", token);
    const body = await requestJson(url, { method: "POST", ...options, headers });
    setFlash(body.message || body.detail || "Sales action completed.", "success-copy");
    await refresh(true);
    return body;
  }

  async function marketingAction(url, confirmation, options = {}) {
    if (confirmation && !window.confirm(confirmation)) return;
    const body = await requestJson(url, { method: "POST", ...options });
    setFlash(body.message || body.detail || "Marketing action completed.", "success-copy");
    await refresh(true);
    return body;
  }

  function fillDatalist(id, options) {
    const list = document.getElementById(id);
    if (!list) return;
    list.replaceChildren();
    (options || []).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      list.append(option);
    });
  }

  function closeDrawers() {
    document.querySelectorAll(".drawer-card").forEach((card) => card.classList.add("hidden"));
  }

  function openDrawer(id) {
    closeDrawers();
    const target = document.getElementById(id);
    if (target) target.classList.remove("hidden");
  }

  function setFilter(kind, value) {
    state[`${kind}Filter`] = value;
    document.querySelectorAll(`[data-${kind}-filter]`).forEach((button) => {
      button.classList.toggle("active", button.getAttribute(`data-${kind}-filter`) === value);
    });
  }

  function setSalesSourceFilter(value) {
    state.salesSourceFilter = value;
    document.querySelectorAll("[data-sales-source-filter]").forEach((button) => {
      button.classList.toggle("active", button.getAttribute("data-sales-source-filter") === value);
    });
  }

  function focusSection(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function formatTime(value) {
    try {
      return new Date(value).toLocaleString([], {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return value || "";
    }
  }

  function healthPill(label, ok, detail) {
    const item = el("div", `health-pill ${ok ? "ok" : "warn"}`);
    item.append(el("span", "", label), el("strong", "", ok ? "READY" : "CHECK"));
    if (detail) item.append(el("small", "", detail));
    return item;
  }

  function renderHealth(overview) {
    const wrap = el("div", "health-strip");
    const sales = overview.sales_doctor || { checks: {} };
    const marketing = overview.marketing_doctor || { checks: {} };
    wrap.append(
      healthPill("Marketing", !!marketing.ok, marketing.ok ? "Pipelines available" : "Doctor reported missing dependencies"),
      healthPill("Sales intake", !!sales.checks?.lead_intake, sales.checks?.lead_intake ? "Tunnel ingress configured" : "SALES_INTAKE_SECRET missing"),
      healthPill("Lead research", !!(sales.checks?.hunter || sales.checks?.apollo || sales.checks?.prospeo || sales.checks?.lusha), sales.checks?.prospeo ? "Prospeo ready" : sales.checks?.lusha ? "Lusha ready" : sales.checks?.apollo ? "Apollo ready" : sales.checks?.hunter ? "Hunter ready" : "Add Hunter, Apollo, Prospeo, or Lusha API key"),
      healthPill("Company intel", !!sales.checks?.company_enrich, sales.checks?.company_enrich ? "CE or PDL ready" : "Add CE_API_KEY or PDL_API_KEY"),
      healthPill("Outbound", !!sales.checks?.outbound_email, sales.checks?.outbound_email ? "Resend ready" : "Email send config missing"),
      healthPill("Inbound replies", !!sales.checks?.inbound_replies, sales.checks?.inbound_replies ? "Worker ready" : "Webhook secret missing"),
    );
    operationsHealth.replaceChildren(wrap);
  }

  function matchesMarketing(campaign) {
    if (pageMode === "sales") return false;
    if (state.marketingFilter === "all") return true;
    return campaign.status === state.marketingFilter;
  }

  function leadHasFailedEvent(lead) {
    return Array.isArray(lead.recent_events)
      && lead.recent_events.some((item) => String(item.event || "") === "resend.email.failed");
  }


  function salesSourceGroup(lead) {
    const source = String(lead.source || "manual").toLowerCase();
    if (["website"].includes(source)) return "website";
    if (["popup_offer", "quiz_funnel", "post_form", "social_form"].includes(source)) return "popup";
    if (["prospeo", "apollo", "lusha", "hunter"].includes(source)) return "research";
    if (source === "manual") return "manual";
    return source;
  }

  function sortSalesLeads(leads) {
    const priority = { website: 0, popup: 1, research: 2, manual: 3 };
    return [...(leads || [])].sort((left, right) => {
      const sourceDiff = (priority[salesSourceGroup(left)] ?? 9) - (priority[salesSourceGroup(right)] ?? 9);
      if (sourceDiff) return sourceDiff;
      return String(right.updated_at || right.created_at || "").localeCompare(String(left.updated_at || left.created_at || ""));
    });
  }

  function matchesSales(lead) {
    if (pageMode === "marketing") return false;
    if (state.salesSourceFilter !== "all" && salesSourceGroup(lead) !== state.salesSourceFilter) return false;
    if (state.salesFilter === "all") return true;
    if (state.salesFilter === "failed") return leadHasFailedEvent(lead);
    return lead.stage === state.salesFilter;
  }

  function matchesSalesView(lead) {
    if (pageMode !== "sales") return true;
    if (salesView === "email") {
      if (focusedSalesLeadId && lead.id !== focusedSalesLeadId) return false;
      return Boolean(lead.email)
        || Boolean(latestDraft(lead))
        || leadHasFailedEvent(lead)
        || ["draft_ready", "approved", "contacted", "scheduled", "replied"].includes(lead.stage);
    }
    return ["new", "enriched", "qualified", "suppressed"].includes(lead.stage);
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
    if (artifact.transcript) body.append(el("div", "artifact-transcript", artifact.transcript));
    if (artifact.scenes?.length) {
      const details = el("details", "artifact-details");
      details.append(el("summary", "", `${artifact.scenes.length} SCENES`));
      const scenes = el("div", "scene-list");
      artifact.scenes.forEach((scene) => {
        const item = el("div", "scene-item");
        item.append(el("div", "scene-label", `SCENE ${scene.number}${scene.time ? ` · ${scene.time}` : ""}`));
        if (scene.headline) item.append(el("strong", "", scene.headline));
        if (scene.text) item.append(el("p", "", scene.text));
        if (scene.visual) item.append(el("div", "scene-visual", `Visual: ${scene.visual}`));
        scenes.append(item);
      });
      details.append(scenes);
      body.append(details);
    }
    return artifactPanel("Campaign script", artifact.status || "CREATED", body);
  }

  function copyArtifacts(copy, variants = {}) {
    const body = el("div", "artifact-body copy-grid");
    Object.entries(copy || {}).forEach(([platform, value]) => {
      const item = el("div", "copy-item");
      item.append(el("div", "scene-label", platform.replaceAll("_", " ")), el("p", "", readable(value)));
      const platformKey = platform.includes("instagram") ? "instagram" : platform.includes("linkedin") ? "linkedin" : platform.includes("x") || platform.includes("twitter") ? "x" : null;
      const options = platformKey ? variants[platformKey] || [] : [];
      if (options.length) {
        const list = el("div", "artifact-variants");
        options.forEach((option, index) => {
          list.append(el("pre", "lead-draft-body compact-variant", `V${index + 1}\n${option}`));
        });
        item.append(list);
      }
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
    return artifactPanel("Media production", artifact.assets_acquired ? "ACQUIRED" : "SEARCH READY", body);
  }

  function eventTimeline(events) {
    const timeline = el("div", "event-timeline");
    (events || []).slice(-7).reverse().forEach((event) => {
      const row = el("div", "event-row");
      row.append(el("time", "", formatTime(event.created_at)), el("span", "", event.event.replaceAll(".", " · ").replaceAll("_", " ")));
      timeline.append(row);
    });
    return timeline;
  }

  function copyText(value, label) {
    const textValue = String(value || "");
    if (!textValue) return Promise.resolve();
    return navigator.clipboard?.writeText(textValue)
      .then(() => setFlash(`${label} copied.`, "success-copy"))
      .catch(() => window.alert(`Copy failed. ${label}: ${textValue}`));
  }

  function manualPostCard(post) {
    const card = el("article", "campaign-card");
    const header = el("div", "campaign-header");
    const title = el("div", "");
    title.append(el("div", "kicker", post.post_id), el("h3", "", post.title || "Manual social post"));
    const statusLabel = String(post.workflow_status || "saved").replaceAll("_", " ").toUpperCase();
    header.append(title, el("span", `status-pill status-selected`, `${String(post.platform || "post").toUpperCase()} · ${statusLabel}`));
    card.append(header);

    const meta = el("div", "campaign-meta");
    meta.textContent = [post.post_type, post.campaign_id, post.utm_campaign, post.source_detail, `${post.asset_count || 0} assets`].filter(Boolean).join(" · ") || "Tracked manual social post";
    card.append(meta);

    const caption = el("div", "lead-draft");
    caption.append(el("div", "sales-health-label", "Generated caption"), el("pre", "lead-draft-body", post.caption || ""));
    card.append(caption);

    const tracked = el("div", "lead-latest");
    const trackedLink = document.createElement("a");
    trackedLink.href = post.tracked_url || post.destination_url;
    trackedLink.target = "_blank";
    trackedLink.rel = "noreferrer";
    trackedLink.textContent = post.tracked_url || post.destination_url;
    tracked.append(el("div", "sales-health-label", `Controlled link · ${post.link_label || "CTA"}`), trackedLink);
    card.append(tracked);

    const bufferBox = el("div", "lead-latest");
    bufferBox.append(el("div", "sales-health-label", "Buffer status"), el("p", "lead-message compact", String(post.buffer_status || "not_started").replaceAll("_", " ")));
    if (post.metadata?.buffer_error) bufferBox.append(el("p", "lead-message compact", post.metadata.buffer_error));
    card.append(bufferBox);

    const attribution = post.attribution || {};
    const stats = el("div", "lead-latest");
    stats.append(el("div", "sales-health-label", "Sales attribution"));
    stats.append(el("div", "", [
      `${attribution.leads || 0} leads`,
      `${attribution.qualified || 0} qualified`,
      `${attribution.contacted || 0} contacted`,
      `${attribution.replied || 0} replied`,
      `${attribution.won || 0} won`,
    ].join(" · ")));
    card.append(stats);

    if (Array.isArray(post.assets) && post.assets.length) {
      const assets = el("div", "lead-latest");
      assets.append(el("div", "sales-health-label", `Uploaded media · ${post.assets.length}`));
      post.assets.forEach((asset) => {
        const row = el("div", "interaction-row");
        const link = document.createElement("a");
        link.href = asset.asset_url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = asset.filename || "asset";
        row.append(link);
        const detail = [];
        if (asset.size_bytes) detail.push(`${asset.size_bytes} bytes`);
        if (asset.content_type) detail.push(asset.content_type);
        if (detail.length) row.append(el("p", "lead-message compact", detail.join(" · ")));
        assets.append(row);
      });
      card.append(assets);
    }

    const actions = el("div", "row-actions");
    const copyCaption = el("button", "mini-action", "COPY CAPTION");
    copyCaption.addEventListener("click", () => copyText(post.caption, "Caption"));
    actions.append(copyCaption);
    const copyLink = el("button", "mini-action secondary", "COPY LINK");
    copyLink.addEventListener("click", () => copyText(post.tracked_url, post.platform === "instagram" ? "Bio link" : "Tracked link"));
    actions.append(copyLink);
    const copyPostId = el("button", "mini-action secondary", "COPY POST ID");
    copyPostId.addEventListener("click", () => copyText(post.post_id, "Post ID"));
    actions.append(copyPostId);
    if ((post.platform === "instagram" || post.platform === "x") && post.buffer_ready && post.buffer_status !== "drafted") {
      const pushBuffer = el("button", "mini-action approve", "PUSH TO BUFFER");
      pushBuffer.addEventListener("click", () => marketingAction(
        `/company/marketing/manual-posts/${post.id}/buffer-draft`,
        `Create a Buffer draft from this saved ${post.post_type || "post"}?`
      ).catch((error) => window.alert(error.message)));
      actions.append(pushBuffer);
    }
    card.append(actions);
    return card;
  }

  function assetPackCard(pack) {
    const card = el("article", "campaign-card");
    const header = el("div", "campaign-header");
    const title = el("div", "");
    title.append(el("div", "kicker", pack.id), el("h3", "", pack.title || pack.id));
    header.append(title, el("span", `status-pill status-selected`, String(pack.status || "ready").replaceAll("_", " ").toUpperCase()));
    card.append(header);
    const planning = pack.planning_source ? ` · ${String(pack.planning_source).toUpperCase()} PLAN` : "";
    card.append(el("div", "campaign-meta", `${String(pack.objective || "awareness").toUpperCase()} · ${pack.scene_count || 0} BACKGROUNDS${planning}`));
    if (pack.input_word_count) {
      const duration = pack.target_duration_seconds ? ` · ${pack.target_duration_seconds}s ${pack.duration_source || "estimated"}` : "";
      card.append(el(
        "div",
        "scene-visual",
        `${pack.input_word_count} words · ${pack.input_paragraph_count} paragraphs${duration} · target ${pack.target_scene_count} · returned ${pack.returned_scene_count}`,
      ));
    }
    if (pack.error) card.append(el("div", "campaign-error", pack.error));
    if (pack.source_excerpt) card.append(el("p", "lead-message", pack.source_excerpt));
    const folder = el("div", "lead-latest");
    folder.append(el("div", "sales-health-label", "Download folder"), el("p", "lead-message compact", pack.download_path || ""));
    card.append(folder);
    if (Array.isArray(pack.scenes) && pack.scenes.length) {
      const details = el("details", "artifact-details");
      details.append(el("summary", "", `${pack.scenes.length} SCENES`));
      const list = el("div", "scene-list");
      pack.scenes.forEach((scene) => {
        const item = el("div", "scene-item");
        item.append(el("div", "scene-label", `SCENE ${scene.number || ""}`));
        if (scene.headline) item.append(el("strong", "", scene.headline));
        if (scene.body) item.append(el("p", "", String(scene.body).slice(0, 200)));
        if (scene.paragraph_start) item.append(el("div", "scene-visual", `Paragraphs ${scene.paragraph_start}–${scene.paragraph_end}`));
        if (scene.pexels_query) item.append(el("div", "scene-visual", `Pexels: ${scene.pexels_query}`));
        list.append(item);
      });
      details.append(list);
      card.append(details);
    }
    if (Array.isArray(pack.assets) && pack.assets.length) {
      const assets = el("div", "lead-latest");
      assets.append(el("div", "sales-health-label", `Downloaded files · ${pack.assets.length}`));
      pack.assets.forEach((asset) => {
        const row = el("div", "interaction-row");
        const link = document.createElement("a");
        link.href = asset.asset_url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = asset.name || asset.relative_path || "asset";
        row.append(link);
        const detail = [];
        if (asset.size_bytes) detail.push(`${asset.size_bytes} bytes`);
        if (asset.relative_path) detail.push(asset.relative_path);
        if (detail.length) row.append(el("p", "lead-message compact", detail.join(" · ")));
        assets.append(row);
      });
      card.append(assets);
    }
    return card;
  }

  function renderAssetPacks(packs) {
    if (!assetPackList || !assetPackCount) return;
    assetPackCount.textContent = `${(packs || []).length} PACKS`;
    assetPackList.replaceChildren();
    if (!(packs || []).length) {
      assetPackList.append(el("div", "muted", "No downloaded asset packs yet."));
      return;
    }
    (packs || []).forEach((pack) => assetPackList.append(assetPackCard(pack)));
  }

  function renderManualPosts(posts) {
    if (!manualPostList || !manualPostCount) return;
    manualPostCount.textContent = `${(posts || []).length} POSTS`;
    manualPostList.replaceChildren();
    if (!(posts || []).length) {
      manualPostList.append(el("div", "muted", "No manual social posts saved yet."));
      return;
    }
    (posts || []).forEach((post) => manualPostList.append(manualPostCard(post)));
  }

  function marketingCard(campaign) {
    const card = el("article", "campaign-card");
    const header = el("div", "campaign-header");
    const title = el("div", "");
    title.append(el("div", "kicker", campaign.id), el("h3", "", campaign.topic));
    header.append(title, el("span", `status-pill status-${campaign.status}`, campaign.status.replaceAll("_", " ")));
    card.append(header);

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
    const buyerLabel = campaign.buyer || "ops teams";
    card.append(el("div", "campaign-meta", `${campaign.objective.toUpperCase()} · ${buyerLabel} · ${campaign.video_platform.toUpperCase()} · ${(campaign.social_platforms || []).join(" + ")} · ${String(campaign.voice_mode || "tts").replaceAll("_", " ").toUpperCase()}`));
    if (campaign.error) card.append(el("div", "campaign-error", campaign.error));

    const artifacts = campaign.artifacts || {};
    if (campaign.status === "needs_voice_recording") {
      const voiceBox = el("div", "lead-latest");
      voiceBox.append(el("div", "sales-health-label", "Real voice intake"));
      voiceBox.append(el("p", "lead-message compact", campaign.voice_recording_uploaded
        ? "Narration uploaded. You can replace it with a new file to re-render."
        : "Upload one narration track. Company Core will render the final review MP4 with your real voice."));
      const uploadRow = el("div", "row-actions");
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".mp3,.wav,.m4a,.aac,.ogg,.webm,.mp4,.mov,audio/*";
      input.className = "field-input";
      const upload = el("button", "mini-action approve", campaign.voice_recording_uploaded ? "REPLACE NARRATION" : "UPLOAD NARRATION");
      upload.addEventListener("click", async () => {
        const file = input.files?.[0];
        if (!file) {
          window.alert("Choose an audio file first.");
          return;
        }
        const form = new FormData();
        form.set("recording", file);
        try {
          const body = await requestJson(`/company/marketing/campaigns/${campaign.id}/voice-recording`, {
            method: "POST",
            body: form,
          });
          setFlash(body.message || "Voice recording uploaded.", "success-copy");
          await refresh(true);
        } catch (error) {
          window.alert(error.message);
        }
      });
      uploadRow.append(input, upload);
      voiceBox.append(uploadRow);
      card.append(voiceBox);
    }
    if (artifacts.script || artifacts.media) {
      const artifactGrid = el("div", "artifact-grid");
      if (artifacts.script) artifactGrid.append(scriptArtifacts(artifacts.script));
      if (artifacts.media) artifactGrid.append(mediaArtifacts(artifacts.media));
      card.append(artifactGrid);
      if (artifacts.script && Object.keys(artifacts.script.platform_copy || {}).length) card.append(copyArtifacts(artifacts.script.platform_copy, artifacts.script.short_caption_variants || {}));
    }

    if (campaign.variants?.length) {
      const variants = el("div", "variant-grid");
      campaign.variants.forEach((variant) => {
        const item = el("article", "variant-card");
        const top = el("div", "variant-top");
        top.append(el("strong", "", variant.voice), el("span", `status-pill status-${variant.status}`, variant.status));
        item.append(top);
        if (variant.status === "ready") {
          const video = document.createElement("video");
          video.controls = true;
          video.preload = "metadata";
          video.playsInline = true;
          video.src = variant.video_url;
          item.append(video);
          item.append(el("div", "variant-meta", `${variant.platform.toUpperCase()} · ${Number(variant.duration_seconds || 0).toFixed(1)}s · ${Number(variant.speed).toFixed(2)}×`));
          const selected = campaign.selected_variant_id === variant.id;
          const select = el("button", selected ? "mini-action selected" : "mini-action", selected ? "SELECTED" : "SELECT VARIANT");
          select.disabled = selected || campaign.status === "drafted" || campaign.status === "drafting";
          select.addEventListener("click", () => marketingAction(
            `/company/marketing/campaigns/${campaign.id}/variants/${variant.id}/select`,
            "Select this render as the campaign video?"
          ).catch((error) => window.alert(error.message)));
          item.append(select);
        } else if (variant.error) {
          item.append(el("div", "variant-error", variant.error));
        } else {
          item.append(el("div", "variant-loading", "Rendering voice, media, and captions…"));
        }
        variants.append(item);
      });
      card.append(variants);
    }

    const actions = el("div", "row-actions");
    if (campaign.status === "needs_voice_recording" && campaign.voice_handoff_url) {
      const downloadHandoff = el("a", "mini-action approve", "VOICE HANDOFF");
      downloadHandoff.href = campaign.voice_handoff_url;
      downloadHandoff.target = "_blank";
      downloadHandoff.rel = "noreferrer";
      actions.append(downloadHandoff);
    }
    if (campaign.status === "needs_campaign_review") {
      const approve = el("button", "mini-action approve", "APPROVE SCRIPT");
      approve.addEventListener("click", () => marketingAction(
        `/company/marketing/campaigns/${campaign.id}/approve-script`,
        "Approve this exact script and continue to media?"
      ).catch((error) => window.alert(error.message)));
      actions.append(approve);

      const regenerate = el("button", "mini-action secondary", "REGENERATE");
      regenerate.addEventListener("click", () => {
        const direction = window.prompt("Describe the new creative direction.");
        if (!direction) return;
        marketingAction(
          `/company/marketing/campaigns/${campaign.id}/regenerate-script`,
          "Regenerate this script with a new angle?",
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
      draft.addEventListener("click", () => marketingAction(
        `/company/marketing/campaigns/${campaign.id}/drafts`,
        "Create Buffer drafts from the selected variant?"
      ).catch((error) => window.alert(error.message)));
      actions.append(draft);
    }
    if (campaign.status === "failed") {
      const retry = el("button", "mini-action danger", campaign.g3_status === "failed" ? "RETRY DRAFTS" : "RETRY PIPELINE");
      retry.addEventListener("click", () => marketingAction(
        `/company/marketing/campaigns/${campaign.id}/retry`,
        "Retry this failed campaign?"
      ).catch((error) => window.alert(error.message)));
      actions.append(retry);
    }
    if (terminalMarketing.has(campaign.status)) {
      const archive = el(
        "button",
        "mini-action secondary",
        pageMode === "history" ? "RESTORE" : "REMOVE"
      );
      archive.addEventListener("click", () => {
        if (pageMode === "history") {
          restoreEntity("marketing", campaign.id);
          setFlash("Campaign restored to operations.", "success-copy");
        } else {
          archiveEntity("marketing", campaign.id);
          setFlash("Campaign moved to history.", "success-copy");
        }
        renderMarketing(state.overview?.marketing_campaigns || []);
      });
      actions.append(archive);
    }
    if (campaign.events?.length) {
      const details = el("details", "event-details");
      details.append(el("summary", "", "LIVE ACTIVITY"), eventTimeline(campaign.events));
      card.append(details);
    }
    card.append(actions);
    return card;
  }

  function latestDraft(lead) {
    return (lead.drafts || [])[0] || null;
  }

  function draftEditor(draft, lead) {
    const editor = el("div", "lead-draft");
    editor.append(el("div", "sales-health-label", `Edit draft ${draft.status}`));
    const subject = document.createElement("input");
    subject.type = "text";
    subject.className = "field-input";
    subject.value = draft.subject || "";
    subject.placeholder = "Email subject";
    const body = document.createElement("textarea");
    body.className = "field-area";
    body.value = draft.body || "";
    body.placeholder = "Email body";
    const actions = el("div", "row-actions");
    const save = el("button", "mini-action approve", "SAVE DRAFT");
    save.addEventListener("click", () => salesAction(
      `/company/sales/drafts/${draft.id}/update`,
      "Save these draft edits?",
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: subject.value, body: body.value }),
      }
    ).then((updated) => {
      if (!updated) return;
      draft.subject = updated.subject;
      draft.body = updated.body;
      lead.draft_preview = null;
      renderSales(state.overview?.sales_leads || [], state.overview?.sales_summary || {});
      setFlash("Draft changes saved.", "success-copy");
    }).catch((error) => window.alert(error.message)));
    actions.append(save);
    editor.append(subject, body, actions);
    return editor;
  }

  function renderLeadInteractions(lead) {
    const interactions = Array.isArray(lead.interactions) && lead.interactions.length
      ? lead.interactions.slice(0, 4)
      : lead.latest_interaction
        ? [lead.latest_interaction]
        : [];
    if (!interactions.length) return null;
    const box = el("div", "lead-latest");
    box.append(el("div", "sales-health-label", "Email conversation"));
    interactions.forEach((interaction) => {
      const meta = interaction.metadata || {};
      const item = el("div", "interaction-row");
      const direction = interaction.direction === "inbound" ? "Reply" : "Sent";
      const parts = [direction, String(interaction.kind || "email").replaceAll("_", " "), formatTime(interaction.created_at)];
      if (meta.source) parts.push(String(meta.source).replaceAll("_", " "));
      item.append(el("div", "history-meta", parts.join(" · ")));
      if (interaction.subject) item.append(el("strong", "", interaction.subject));
      const routing = [];
      if (meta.from_email) routing.push(`from ${meta.from_email}`);
      if (meta.to_email) routing.push(`to ${meta.to_email}`);
      if (meta.reply_to) routing.push(`reply-to ${meta.reply_to}`);
      if (interaction.provider_message_id) routing.push(`id ${interaction.provider_message_id}`);
      if (routing.length) item.append(el("p", "lead-message compact", routing.join(" · ")));
      if (interaction.body) item.append(el("p", "lead-message compact", String(interaction.body).slice(0, 280)));
      box.append(item);
    });
    return box;
  }

  function leadCanArchive(lead) {
    if (terminalSales.has(lead.stage)) return true;
    return leadHasFailedEvent(lead);
  }

  function renderLeadEvents(lead) {
    const events = (lead.recent_events || []).filter((item) =>
      String(item.event || "").startsWith("resend.email.") || String(item.event || "") === "outreach.sent"
    ).slice(0, 5);
    if (!events.length) return null;
    const box = el("div", "lead-latest");
    box.append(el("div", "sales-health-label", "Delivery activity"));
    events.forEach((item) => {
      const payload = item.payload || {};
      const row = el("div", "interaction-row");
      row.append(el("div", "history-meta", `${String(item.event || "").replaceAll(".", " · " ).replaceAll("_", " ")} · ${formatTime(item.created_at)}`));
      const detail = [];
      if (payload.from_email) detail.push(`from ${payload.from_email}`);
      if (payload.to_email) detail.push(`to ${payload.to_email}`);
      if (payload.reply_to) detail.push(`reply-to ${payload.reply_to}`);
      if (payload.subject) detail.push(String(payload.subject));
      if (detail.length) row.append(el("p", "lead-message compact", detail.join(" · ")));
      box.append(row);
    });
    return box;
  }

  function renderMeetingStatus(lead) {
    const meeting = lead.metadata?.meeting;
    if (!meeting || meeting.status !== "scheduled") return null;
    const box = el("div", "lead-latest");
    box.append(el("div", "sales-health-label", "Meeting alignment"));
    const parts = ["Manual schedule saved"];
    if (meeting.scheduled_for) parts.push(`When ${meeting.scheduled_for}`);
    if (meeting.updated_at) parts.push(`Updated ${formatTime(meeting.updated_at)}`);
    box.append(el("div", "", parts.join(" · ")));
    if (meeting.note) box.append(el("p", "lead-message compact", meeting.note));
    return box;
  }

  function salesCard(lead) {
    const showDiscovery = pageMode !== "sales" || salesView === "discovery";
    const showEmail = pageMode !== "sales" || salesView === "email";
    const isCompanyCandidate = Boolean(lead.metadata?.company_candidate);
    const card = el("article", "lead-card");
    card.id = `sales-lead-${lead.id}`;
    const header = el("div", "lead-header");
    const title = el("div", "");
    title.append(el("div", "kicker", lead.id), el("h3", "", lead.full_name || lead.company || lead.email || lead.id));
    header.append(title, el("span", `status-pill status-${lead.stage}`, lead.stage.replaceAll("_", " ")));
    card.append(header);

    const meta = el("div", "lead-meta");
    [
      `score ${lead.lead_score || 0}`,
      lead.company || "no company",
      lead.job_title || "no title",
      lead.email || "no email",
      lead.source || "manual",
      lead.source_detail ? `detail ${lead.source_detail}` : null,
    ].filter(Boolean).forEach((item) => meta.append(el("span", "chip", item)));
    card.append(meta);
    if (lead.message) card.append(el("p", "lead-message", lead.message));
    if (showDiscovery && (lead.source === "website" || salesSourceGroup(lead) === "popup")) {
      const intake = el("div", "lead-latest");
      intake.append(el("div", "sales-health-label", "Inbound source"));
      const details = [];
      details.push(lead.source === "website" ? "Website signup" : "Popup or quiz lead");
      if (lead.utm_source) details.push(`utm ${lead.utm_source}`);
      if (lead.utm_campaign) details.push(`campaign ${lead.utm_campaign}`);
      if (lead.company_domain) details.push(lead.company_domain);
      intake.append(el("div", "", details.join(" · ")));
      card.append(intake);
    }

    if (showEmail) {
      const conversationBox = renderLeadInteractions(lead);
      if (conversationBox) card.append(conversationBox);

      const deliveryBox = renderLeadEvents(lead);
      if (deliveryBox) card.append(deliveryBox);

      const meetingBox = renderMeetingStatus(lead);
      if (meetingBox) card.append(meetingBox);
    }

    const draft = latestDraft(lead);
    if (showEmail && draft) {
      const draftBox = el("div", "lead-draft");
      draftBox.append(el("div", "sales-health-label", `Draft ${draft.status}`), el("strong", "", draft.subject || "Draft"), el("pre", "lead-draft-body", draft.body || ""));
      card.append(draftBox);
      if (draft.status === "draft" || draft.status === "approved") card.append(draftEditor(draft, lead));
    }

    const companyProfile = lead.company_profile || null;
    const draftPreview = lead.draft_preview || null;
    if (showDiscovery && companyProfile?.summary) {
      const companyBox = el("div", "lead-latest");
      companyBox.append(el("div", "sales-health-label", "Company profile"));
      const summary = companyProfile.summary || {};
      const companyParts = [];
      if (summary.industry) companyParts.push(summary.industry);
      if (summary.employee_range || summary.employee_count) companyParts.push(String(summary.employee_range || summary.employee_count));
      if (summary.location?.country) companyParts.push(summary.location.country);
      if (companyProfile.provider) companyParts.push(String(companyProfile.provider).toUpperCase());
      companyBox.append(el("div", "", companyParts.length ? companyParts.join(" · ") : "Saved company profile available."));
      if (summary.description) companyBox.append(el("p", "lead-message compact", summary.description));
      if (Array.isArray(summary.keywords) && summary.keywords.length) companyBox.append(el("p", "lead-message compact", `Keywords: ${summary.keywords.slice(0, 6).join(", ")}`));
      if (summary.signals?.operational_focus) companyBox.append(el("p", "lead-message compact", `Focus: ${summary.signals.operational_focus}`));
      card.append(companyBox);
    }

    if (showEmail && draftPreview?.preview) {
      const previewBox = el("div", "lead-draft");
      previewBox.append(el("div", "sales-health-label", "Draft preview"));
      if (draftPreview.context?.company_context?.summary_line) previewBox.append(el("div", "lead-preview-meta", draftPreview.context.company_context.summary_line));
      if (Array.isArray(draftPreview.context?.company_context?.suggested_pain_points) && draftPreview.context.company_context.suggested_pain_points.length) {
        previewBox.append(el("p", "lead-message compact", `Angle: ${draftPreview.context.company_context.suggested_pain_points[0]}`));
      }
      previewBox.append(el("strong", "", draftPreview.preview.subject || "Preview subject"), el("pre", "lead-draft-body", draftPreview.preview.body || ""));
      if (draftPreview.preview.rationale) previewBox.append(el("p", "lead-message compact", `Why this draft: ${draftPreview.preview.rationale}`));
      card.append(previewBox);
    }

    const contactProfile = lead.contact_profile || {};
    if (showDiscovery) {
      const contactBox = el("div", "lead-latest");
      contactBox.append(el("div", "sales-health-label", "Contact status"));
      const contactParts = [];
      if (contactProfile.status) contactParts.push(`Status ${String(contactProfile.status).toUpperCase()}`);
      if (contactProfile.provider) contactParts.push(`Source ${String(contactProfile.provider).toUpperCase()}`);
      if (contactProfile.email_ready) contactParts.push("EMAIL READY");
      if (contactProfile.whatsapp_ready) contactParts.push("WHATSAPP READY");
      if (contactProfile.resolved_at) contactParts.push(`Checked ${formatTime(contactProfile.resolved_at)}`);
      contactBox.append(el("div", "", contactParts.length ? contactParts.join(" · ") : "No contact data resolved yet."));
      if (contactProfile.email) {
        contactBox.append(el("p", "lead-message compact", `Direct email: ${contactProfile.email}`));
      }
      if (contactProfile.whatsapp_candidate) {
        contactBox.append(el("p", "lead-message compact", `WhatsApp candidate: ${contactProfile.whatsapp_candidate}`));
      }
      if (contactProfile.needs_generic_fallback) {
        contactBox.append(el("p", "lead-message compact", "No direct contact found yet. Generic fallback draft will be needed unless a later resolver finds email or mobile."));
      }
      card.append(contactBox);
    }

    const actions = el("div", "row-actions");
    if (showDiscovery && lead.stage !== "suppressed") {
      if (lead.company_domain) {
        if (isCompanyCandidate) {
          const findContact = el("button", "mini-action approve", "FIND ONE CONTACT");
          findContact.addEventListener("click", () => {
            if (prospectForm?.elements?.domain) prospectForm.elements.domain.value = lead.company_domain;
            openDrawer("prospect-launcher");
            setFlash(`Domain ready: ${lead.company_domain}. Choose the provider, then run one contact lookup.`, "muted");
          });
          actions.append(findContact);
        }

        const resolveCompany = el("button", "mini-action secondary", "RESOLVE COMPANY");
        resolveCompany.addEventListener("click", () => salesAction(
          `/company/sales/leads/${lead.id}/resolve-company?provider=auto`,
          "Resolve and cache the company profile for this lead?"
        ).then((body) => {
          if (!body) return;
          if (body.cached) {
            setFlash("Using cached company profile for this lead.", "muted");
            return;
          }
          const summary = body.company_profile?.summary || {};
          const industry = summary.industry || "no industry";
          const size = summary.employee_range || summary.employee_count || "no size";
          setFlash(`Company profile saved: ${industry} · ${size}.`, "success-copy");
        }).catch((error) => window.alert(error.message)));
        actions.append(resolveCompany);

        const refreshCompany = el("button", "mini-action secondary", "REFRESH COMPANY");
        refreshCompany.addEventListener("click", () => salesAction(
          `/company/sales/leads/${lead.id}/resolve-company?provider=auto&force=true`,
          "Re-run company profile resolution for this lead?"
        ).then((body) => {
          if (!body) return;
          const summary = body.company_profile?.summary || {};
          const industry = summary.industry || "no industry";
          const size = summary.employee_range || summary.employee_count || "no size";
          setFlash(`Company profile saved: ${industry} · ${size}.`, "success-copy");
        }).catch((error) => window.alert(error.message)));
        actions.append(refreshCompany);
      }

      if (!isCompanyCandidate && lead.metadata?.prospeo_person_id) {
        const resolveContact = el("button", "mini-action", "RESOLVE CONTACT");
        resolveContact.addEventListener("click", () => salesAction(
          `/company/sales/leads/${lead.id}/resolve-contact?provider=prospeo`,
          "Resolve direct contact details for this lead with Prospeo?"
        ).then((body) => {
          if (!body) return;
          if (body.cached) {
            setFlash("Using cached contact resolution for this lead.", "muted");
            return;
          }
          const emailFound = body.contact?.email ? `email ${body.contact.email}` : "no email";
          const phoneFound = body.contact?.phone ? `mobile ${body.contact.phone}` : "no mobile";
          const readiness = body.contact?.ready_for_outreach ? "ready for outreach" : "fallback needed";
          setFlash(`Contact resolution ${body.status}: ${emailFound} · ${phoneFound} · ${readiness}.`, body.status === "missing" ? "error-copy" : "success-copy");
        }).catch((error) => window.alert(error.message)));
        actions.append(resolveContact);

        const refreshContact = el("button", "mini-action secondary", "REFRESH CONTACT");
        refreshContact.addEventListener("click", () => salesAction(
          `/company/sales/leads/${lead.id}/resolve-contact?provider=prospeo&force=true`,
          "Re-run contact resolution for this lead?"
        ).then((body) => {
          if (!body) return;
          const emailFound = body.contact?.email ? `email ${body.contact.email}` : "no email";
          const phoneFound = body.contact?.phone ? `mobile ${body.contact.phone}` : "no mobile";
          const readiness = body.contact?.ready_for_outreach ? "ready for outreach" : "fallback needed";
          setFlash(`Contact resolution ${body.status}: ${emailFound} · ${phoneFound} · ${readiness}.`, body.status === "missing" ? "error-copy" : "success-copy");
        }).catch((error) => window.alert(error.message)));
        actions.append(refreshContact);
      }
    }
    if (showDiscovery && lead.email && lead.stage !== "suppressed") {
      const openEmail = el("button", "mini-action approve", draft ? "OPEN EMAIL" : "START EMAIL");
      openEmail.addEventListener("click", () => {
        window.location.href = `/operations/sales/email?lead=${encodeURIComponent(lead.id)}#sales-lead-${encodeURIComponent(lead.id)}`;
      });
      actions.append(openEmail);
    }
    if (showEmail && lead.email && lead.stage !== "suppressed") {
      const previewButton = el("button", "mini-action secondary", "PREVIEW DRAFT");
      previewButton.addEventListener("click", () => salesAction(
        `/company/sales/leads/${lead.id}/draft-preview`,
        "Generate a preview draft using the cleaned lead and company context?"
      ).then((body) => {
        if (!body) return;
        lead.draft_preview = body;
        renderSales(state.overview?.sales_leads || [], state.overview?.sales_summary || {});
        setFlash(`Preview ready: ${body.preview?.subject || "draft prepared"}.`, "success-copy");
      }).catch((error) => window.alert(error.message)));
      actions.append(previewButton);

      const draftButton = el("button", "mini-action", draft ? "REDRAFT" : "CREATE DRAFT");
      draftButton.addEventListener("click", () => salesAction(
        `/company/sales/leads/${lead.id}/draft`,
        "Generate an outreach draft for this lead?"
      ).catch((error) => window.alert(error.message)));
      actions.append(draftButton);
    }
    if (showEmail && draft?.status === "draft") {
      const approve = el("button", "mini-action approve", "APPROVE DRAFT");
      approve.addEventListener("click", () => salesAction(
        `/company/sales/drafts/${draft.id}/approve`,
        "Approve this draft for sending?"
      ).catch((error) => window.alert(error.message)));
      actions.append(approve);
    }
    if (showEmail && draft?.status === "approved") {
      const send = el("button", "mini-action approve", "SEND EMAIL");
      send.addEventListener("click", () => salesAction(
        `/company/sales/drafts/${draft.id}/send`,
        "Send this approved draft now?"
      ).catch((error) => window.alert(error.message)));
      actions.append(send);
    }
    if (showEmail && ["contacted", "replied", "scheduled"].includes(lead.stage)) {
      const scheduleMeeting = el("button", "mini-action approve", lead.stage === "scheduled" ? "UPDATE MEETING" : "MARK MEETING");
      scheduleMeeting.addEventListener("click", () => {
        const scheduledFor = window.prompt("Meeting date/time or note (optional)", lead.metadata?.meeting?.scheduled_for || "");
        if (scheduledFor === null) return;
        const note = window.prompt("Alignment note (optional)", lead.metadata?.meeting?.note || "");
        if (note === null) return;
        salesAction(
          `/company/sales/leads/${lead.id}/schedule-meeting`,
          "Save this meeting alignment on the lead?",
          {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scheduled_for: scheduledFor, note }),
          }
        ).catch((error) => window.alert(error.message));
      });
      actions.append(scheduleMeeting);
    }
    if (lead.stage !== "suppressed") {
      const suppress = el("button", "mini-action danger", "SUPPRESS");
      suppress.addEventListener("click", () => {
        const reason = window.prompt("Suppression reason", "founder_request");
        if (!reason) return;
        salesAction(
          `/company/sales/leads/${lead.id}/suppress?reason=${encodeURIComponent(reason)}`,
          "Suppress this lead and block future outreach?"
        ).catch((error) => window.alert(error.message));
      });
      actions.append(suppress);
    }
    if (leadCanArchive(lead)) {
      const archive = el(
        "button",
        "mini-action secondary",
        pageMode === "history" ? "RESTORE" : "REMOVE"
      );
      archive.addEventListener("click", () => {
        if (pageMode === "history") {
          restoreEntity("sales", lead.id);
          setFlash("Lead restored to operations.", "success-copy");
        } else {
          archiveEntity("sales", lead.id);
          setFlash("Lead moved to history.", "success-copy");
        }
        renderSales(state.overview?.sales_leads || [], state.overview?.sales_summary || {});
      });
      actions.append(archive);
    }
    card.append(actions);
    return card;
  }

  function renderMarketing(campaigns) {
    if (!marketingList || !marketingCount) return;
    const filtered = visibleMarketing(campaigns).filter(matchesMarketing);
    marketingCount.textContent = `${filtered.length} CAMPAIGN${filtered.length === 1 ? "" : "S"}`;
    marketingList.replaceChildren();
    if (!filtered.length) {
      marketingList.append(el("div", "muted", pageMode === "sales" ? "Marketing is available on its own page." : "No campaigns match this filter."));
      return;
    }
    filtered.forEach((campaign) => marketingList.append(marketingCard(campaign)));
  }

  function renderSales(leads, summary) {
    if (!salesList || !salesCount) return;
    const visible = sortSalesLeads(visibleSales(leads));
    const lane = visible.filter(matchesSalesView);
    const filtered = lane.filter(matchesSales);
    const total = lane.length;
    salesCount.textContent = `${filtered.length} / ${total} LEADS`;
    salesList.replaceChildren();
    if (!filtered.length) {
      const empty = pageMode === "marketing"
        ? "Sales is available on its own page."
        : salesView === "email"
          ? focusedSalesLeadId
            ? "The selected lead has no email yet. Resolve or import a contact before drafting."
            : "No contacts are ready for this email lane."
          : "No leads match this discovery filter.";
      salesList.append(el("div", "muted", empty));
      return;
    }
    filtered.forEach((lead) => {
      try {
        salesList.append(salesCard(lead));
      } catch (error) {
        console.error("Unable to render sales lead", lead?.id, error);
        const failed = el("article", "lead-card campaign-error");
        failed.append(
          el("strong", "", lead?.company || lead?.full_name || lead?.email || lead?.id || "Lead"),
          el("p", "lead-message compact", `This lead could not be rendered: ${error?.message || "invalid lead data"}`),
        );
        salesList.append(failed);
      }
    });
  }

  function matchesHistory(item) {
    if (state.historyFilter !== "all" && item.domain !== state.historyFilter) return false;
    if (!state.search) return true;
    const haystack = `${item.entity_id || ""} ${item.entity_label || ""} ${item.event || ""} ${item.summary || ""}`.toLowerCase();
    return haystack.includes(state.search);
  }

  function renderHistory(history) {
    if (!historyList || !historyCount) return;
    const filtered = history.filter(matchesHistory);
    historyCount.textContent = `${filtered.length} EVENTS`;
    historyList.replaceChildren();
    if (!filtered.length) {
      historyList.append(el("div", "muted", "No history matches this filter."));
      return;
    }
    filtered.forEach((item) => {
      const card = el("button", `history-item domain-${item.domain}`);
      card.type = "button";
      card.addEventListener("click", () => {
        if (item.domain === "sales") {
          setFilter("sales", "all");
          focusSection("sales-queue");
        } else {
          setFilter("marketing", "all");
          focusSection("marketing-queue");
        }
      });
      card.append(
        el("div", "history-meta", `${item.domain.toUpperCase()} · ${formatTime(item.created_at)}`),
        el("strong", "", item.entity_label || item.entity_id || item.domain),
        el("div", "history-event", item.event.replaceAll(".", " · ").replaceAll("_", " ")),
      );
      if (item.summary) card.append(el("p", "lead-message compact", item.summary));
      historyList.append(card);
    });
  }

  async function refresh(force = false) {
    try {
      const overview = await requestJson(overviewUrl, { cache: "no-store" });
      state.overview = overview;
      projectChip.textContent = overview.active_project?.slug || "NO PROJECT";
      renderHealth(overview);
      renderMarketing(overview.marketing_campaigns || []);
      renderSales(overview.sales_leads || [], overview.sales_summary || {});
      renderHistory((overview.history || []).filter((item) => pageMode === "sales" ? item.domain === "sales" : pageMode === "marketing" ? item.domain === "marketing" : true));
      renderManualPosts(overview.manual_posts || []);
      renderAssetPacks(overview.asset_packs || []);
      if (force) setFlash("Operational state refreshed.", "muted");
      window.clearTimeout(state.refreshTimer);
      state.refreshTimer = window.setTimeout(refresh, 10000);
    } catch (error) {
      setFlash(error.message, "error-copy");
      operationsHealth?.replaceChildren(el("div", "campaign-error", error.message));
      marketingList?.replaceChildren(el("div", "campaign-error", error.message));
      salesList?.replaceChildren(el("div", "campaign-error", error.message));
      historyList?.replaceChildren(el("div", "campaign-error", error.message));
      manualPostList?.replaceChildren(el("div", "campaign-error", error.message));
      assetPackList?.replaceChildren(el("div", "campaign-error", error.message));
      if (marketingCount) marketingCount.textContent = "OFFLINE";
      if (salesCount) salesCount.textContent = "OFFLINE";
      if (historyCount) historyCount.textContent = "OFFLINE";
      if (manualPostCount) manualPostCount.textContent = "OFFLINE";
      if (assetPackCount) assetPackCount.textContent = "OFFLINE";
      window.clearTimeout(state.refreshTimer);
      state.refreshTimer = window.setTimeout(refresh, 10000);
    }
  }

  document.querySelectorAll("[data-drawer]").forEach((button) => {
    button.addEventListener("click", () => {
      const postType = button.getAttribute("data-manual-post-type");
      if (postType && manualPostForm?.elements?.post_type) {
        manualPostForm.elements.post_type.value = postType;
        const media = manualPostForm.elements.assets;
        if (media) {
          media.accept = postType === "video" ? "video/mp4" : "image/png";
          media.multiple = postType !== "video";
        }
      }
      openDrawer(button.getAttribute("data-drawer"));
    });
  });
  document.querySelectorAll("[data-close-drawer]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.getAttribute("data-close-drawer"))?.classList.add("hidden"));
  });
  document.querySelectorAll("[data-sales-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      setFilter("sales", button.getAttribute("data-sales-filter"));
      renderSales(state.overview?.sales_leads || [], state.overview?.sales_summary || {});
      const focus = button.getAttribute("data-focus");
      if (focus) focusSection(focus);
    });
  });
  document.querySelectorAll("[data-sales-source-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      setSalesSourceFilter(button.getAttribute("data-sales-source-filter") || "all");
      renderSales(state.overview?.sales_leads || [], state.overview?.sales_summary || {});
      const focus = button.getAttribute("data-focus");
      if (focus) focusSection(focus);
    });
  });
  document.querySelectorAll("[data-marketing-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      setFilter("marketing", button.getAttribute("data-marketing-filter"));
      renderMarketing(state.overview?.marketing_campaigns || []);
      const focus = button.getAttribute("data-focus");
      if (focus) focusSection(focus);
    });
  });
  document.querySelectorAll("[data-history-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.historyFilter = button.getAttribute("data-history-filter") || "all";
      document.querySelectorAll("[data-history-filter]").forEach((target) => target.classList.toggle("active", target === button));
      renderHistory(state.overview?.history || []);
    });
  });

  historySearch?.addEventListener("input", () => {
    state.search = historySearch.value.trim().toLowerCase();
    renderHistory(state.overview?.history || []);
  });

  saveToken?.addEventListener("click", rememberToken);
  globalRefresh?.addEventListener("click", () => refresh(true));
  marketingForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(marketingForm);
    const socialPlatforms = form.getAll("social_platforms").map((item) => String(item));
    if (!socialPlatforms.length) {
      window.alert("Choose at least one social platform.");
      return;
    }
    await marketingAction(
      "/company/marketing/campaigns",
      "Launch this new marketing campaign?",
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          objective: String(form.get("objective") || ""),
          brief: String(form.get("brief") || ""),
          social_platforms: socialPlatforms,
          video_platform: String(form.get("video_platform") || ""),
          voice_mode: String(form.get("voice_mode") || "tts"),
          voice_transcript: String(form.get("voice_transcript") || ""),
        }),
      }
    ).catch((error) => window.alert(error.message));
    closeDrawers();
    focusSection("marketing-queue");
  });

  leadForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(leadForm);
    await salesAction(
      "/company/sales/leads",
      "Create this manual lead?",
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name: String(form.get("full_name") || ""),
          email: String(form.get("email") || ""),
          company: String(form.get("company") || ""),
          job_title: String(form.get("job_title") || ""),
          country: String(form.get("country") || ""),
          message: String(form.get("message") || ""),
          consent: form.get("consent") === "on",
          source: "manual",
        }),
      }
    ).catch((error) => window.alert(error.message));
    closeDrawers();
    focusSection("sales-queue");
  });

  prospectForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(prospectForm);
    const provider = String(form.get("provider") || "hunter");
    await salesAction(
      "/company/sales/prospect/domain",
      `Import ${provider} prospects from ${String(form.get("domain") || "")}?`,
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          domain: String(form.get("domain") || ""),
          limit: 1,
        }),
      }
    ).catch((error) => window.alert(error.message));
    closeDrawers();
    focusSection("sales-queue");
  });

  researchForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(researchForm);
    const industry = String(form.get("industry") || "").trim();
    const location = String(form.get("location") || "").trim() || null;
    const body = await salesAction(
      "/company/sales/research/leads",
      `Run combined market research for ${industry}?`,
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          industry,
          location,
          limit_per_provider: Number(form.get("limit_per_provider") || 5),
        }),
      }
    ).catch((error) => window.alert(error.message));
    if (!body) return;
    const imported = Array.isArray(body.results) ? body.results.length : 0;
    const providers = body.providers || {};
    const providerSummary = Object.entries(providers)
      .map(([name, count]) => `${name}: ${count}`)
      .join(" · ");
    if (imported === 0) {
      const warning = Array.isArray(body.warnings) && body.warnings.length ? ` ${body.warnings.join(" | ")}` : "";
      setFlash(`No leads imported from the combined market scan.${warning}`, "error-copy");
    } else {
      setFlash(`Saved ${imported} distinct companies across ${providerSummary}. Contact lookup remains on demand.`, "success-copy");
    }
    closeDrawers();
    focusSection("sales-queue");
  });

  assetPackForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(assetPackForm);
    await marketingAction(
      "/company/marketing/asset-packs",
      "Pull and download scene media for this script?",
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          objective: String(form.get("objective") || "awareness"),
          script: String(form.get("script") || ""),
        }),
      }
    ).catch((error) => window.alert(error.message));
    closeDrawers();
    focusSection("asset-packs-queue");
  });

  manualPostForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(manualPostForm);
    const platform = String(form.get("platform") || "instagram");
    const postType = String(form.get("post_type") || "carousel");
    const files = form.getAll("assets").filter((item) => item instanceof File && item.size > 0);
    if (!files.length) {
      window.alert(postType === "video" ? "Upload one MP4 video." : "Upload at least one PNG carousel asset.");
      return;
    }
    if (postType === "video" && files.length !== 1) {
      window.alert("Video posts accept exactly one MP4.");
      return;
    }
    if (!window.confirm(`Save this ${platform} ${postType} post?`)) return;
    try {
      const session = await requestJson("/company/marketing/manual-posts/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform,
          post_type: postType,
          destination_url: String(form.get("destination_url") || ""),
          link_label: String(form.get("link_label") || "ops audit"),
        }),
      });
      const postId = session.post?.id;
      if (!postId) throw new Error("manual post session did not return an id");
      setFlash(`${postType === "video" ? "Video" : "Carousel"} record created. Uploading 1/${files.length}…`, "muted");
      for (let index = 0; index < files.length; index += 1) {
        const uploadBody = new FormData();
        uploadBody.set("asset", files[index]);
        await requestJson(`/company/marketing/manual-posts/${postId}/assets`, {
          method: "POST",
          body: uploadBody,
        });
        setFlash(`Saved ${index + 1}/${files.length} media file${files.length === 1 ? "" : "s"}.`, "muted");
      }
      const finalized = await requestJson(`/company/marketing/manual-posts/${postId}/finalize`, {
        method: "POST",
      });
      setFlash(finalized.message || "Post saved locally and marked ready for Buffer handoff.", "success-copy");
      await refresh(true);
      manualPostForm.reset();
      closeDrawers();
      focusSection("manual-posts-queue");
    } catch (error) {
      window.alert(error.message);
      setFlash(error.message, "error-copy");
    }
  });

  setSalesSourceFilter(state.salesSourceFilter);

  const savedToken = window.sessionStorage.getItem(tokenKey);
  if (savedToken && tokenInput) tokenInput.value = savedToken;

  autosizeAll();
  tickClock();
  refresh();
})();
