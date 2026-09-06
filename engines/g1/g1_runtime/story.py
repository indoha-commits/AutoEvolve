from __future__ import annotations

import os
import re

from .models import DraftCampaign, Slide
from .validation import ABSTRACT_IMAGE_LANGUAGE, CLAIM_LANGUAGE, GENERIC_COPY


ASSET_BY_PURPOSE = {
    "cover": "stock_photo",
    "evidence": "document_composite",
    "operational_complexity": "route_diagram",
    "failure_point": "document_composite",
    "control_system": "product_ui",
    "outcome_cta": "branded_end_card",
}

SAFE_SLIDE_COPY = {
    "cover": (
        "CARGO AND RECORDS MOVE SEPARATELY",
        "Cargo and its records can move through separate operational channels.",
        "Freight operator beside cargo documents and a truck.",
        ["East Africa freight operator cargo documents"],
    ),
    "evidence": (
        "SEPARATE CHANNELS HOLD SEPARATE COPIES",
        "Email, messaging and operations folders may contain separate document copies.",
        "Three dated shipping document copies arranged on a desk.",
        ["dated shipping document copies office desk"],
    ),
    "operational_complexity": (
        "SEVERAL TEAMS USE THE CARGO RECORD",
        "Operations, documentation and clearing teams each work around the cargo record.",
        "Three freight roles connected around one cargo record.",
        ["East Africa freight operations team office"],
    ),
    "failure_point": (
        "WHICH FILE IS CURRENT?",
        "The next operator first identifies which file is current.",
        "Two dated shipping document versions compared side by side.",
        ["two dated shipping documents compared desk"],
    ),
}

SAFE_NARRATIVE_LEADS = {
    "lost_pdfs_in_whatsapp": "Freight documents can sit across messaging and operations channels.",
    "outdated_document_versions": "Freight teams work with document copies across several operational handoffs.",
    "duplicate_data_entry": "Cargo details can be entered by several operational roles.",
    "no_authoritative_cargo_record": "Operational teams need a shared reference for each cargo movement.",
    "document_validation_delays": "Document validation passes through several operational roles.",
    "client_status_calls": "Clients and internal teams use different shipment views.",
    "missing_milestone_accountability": "Cargo milestones pass between several operational owners.",
    "cross_border_handoffs": "Cross-border cargo records pass through several operational handoffs.",
    "cargo_audit_trail": "Cargo activity includes documents, milestones and operational updates.",
    "internal_vs_client_visibility": "Internal teams and clients use different shipment views.",
    "responsible_logistics_ai": "Reliable logistics workflows begin with governed operational records.",
    "port_to_warehouse_coordination": "Cargo records travel from the port through inland operations.",
}

BRAND_NAME = os.getenv("COMPANY_NAME", "Example Company").strip() or "Example Company"
BRAND_UPPER = BRAND_NAME.upper()

CONTROL_HEADLINES = {
    "product.control_layer": f"{BRAND_UPPER} CONNECTS THE CARGO RECORD",
    "product.internal_dashboard": f"{BRAND_UPPER} SHOWS THE OPERATIONS VIEW",
    "product.client_dashboard": f"{BRAND_UPPER} SHOWS THE CLIENT VIEW",
    "product.document_upload": f"{BRAND_UPPER} LINKS DOCUMENTS TO CARGO",
    "product.document_validation": f"{BRAND_UPPER} SHOWS VALIDATION STATES",
    "product.milestones": f"{BRAND_UPPER} TRACKS CARGO MILESTONES",
    "product.audit_trail": f"{BRAND_UPPER} MAINTAINS CARGO HISTORY",
    "product.digital_archive": f"{BRAND_UPPER} INCLUDES A DOCUMENT ARCHIVE",
    "product.web_based": f"{BRAND_UPPER} IS WEB BASED",
}

CONTROL_CAPTIONS = {
    "product.control_layer": f"{BRAND_NAME} provides one operational control layer for cargo, documents, milestones and client visibility.",
    "product.internal_dashboard": f"{BRAND_NAME} includes an internal operations dashboard.",
    "product.client_dashboard": f"{BRAND_NAME} includes a client dashboard for shipment visibility.",
    "product.document_upload": f"{BRAND_NAME} allows documents to be uploaded and linked to cargo records.",
    "product.document_validation": f"{BRAND_NAME} supports document validation and verification states.",
    "product.milestones": f"{BRAND_NAME} tracks cargo milestones with timestamps.",
    "product.audit_trail": f"{BRAND_NAME} maintains cargo activity and audit history.",
    "product.digital_archive": f"{BRAND_NAME} plans include a digital document archive.",
    "product.web_based": f"{BRAND_NAME} is web based.",
}


def _contains_cta(slide: Slide) -> bool:
    text = (slide.headline + " " + slide.body).lower()
    return "book a walkthrough" in text or "7-day trial" in text or "7 day trial" in text


def _contains_product(slide: Slide) -> bool:
    text = (slide.headline + " " + slide.body).lower()
    return BRAND_NAME.lower() in text or "control layer" in text or any(
        claim_id.startswith("product.") for claim_id in slide.claim_ids
    )


def normalize_story(draft: DraftCampaign, cta_registry: dict) -> DraftCampaign:
    """Enforce sequence and offer invariants without inventing editorial claims."""
    slides = list(draft.slides)
    if len(slides) not in {5, 6}:
        return draft

    cta = next((slide for slide in reversed(slides) if _contains_cta(slide)), slides[-1])
    available = [slide for slide in slides if slide is not cta]
    product = next((slide for slide in reversed(available) if _contains_product(slide)), available[-1])
    available = [slide for slide in available if slide is not product]
    cover = next((slide for slide in available if slide.purpose == "cover"), available[0])
    middle = [slide for slide in available if slide is not cover]

    ordered = [cover, *middle, product, cta]
    purposes = (["cover", "operational_complexity", "failure_point", "control_system", "outcome_cta"]
                if len(ordered) == 5 else
                ["cover", "evidence", "operational_complexity", "failure_point", "control_system", "outcome_cta"])
    for number, (slide, purpose) in enumerate(zip(ordered, purposes), 1):
        slide.number = number
        slide.purpose = purpose
        slide.asset_strategy = ASSET_BY_PURPOSE[purpose]
        if slide.asset_strategy in {"product_ui", "branded_end_card"}:
            slide.image_queries = []

    offer_id = "offer.trial_7_day" if draft.objective == "trial" else "offer.walkthrough"
    cta.claim_ids = [claim_id for claim_id in cta.claim_ids if not claim_id.startswith("offer.")]
    cta.claim_ids.append(offer_id)
    cta.body = cta_registry[draft.objective]["copy"]
    cta.headline = cta_registry[draft.objective]["label"].upper()
    for slide in ordered[:-1]:
        slide.claim_ids = [claim_id for claim_id in slide.claim_ids if not claim_id.startswith("offer.")]

    draft.claim_ids = [claim_id for claim_id in draft.claim_ids if not claim_id.startswith("offer.")]
    for field in ("instagram_caption", "x_post"):
        original = getattr(draft.platform_copy, field)
        sentences = [sentence.strip() for sentence in original.split(".") if sentence.strip()]
        cta_sentences = [sentence.strip() for sentence in cta_registry[draft.objective]["copy"].split(".") if sentence.strip()]
        cta_keys = {re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip() for sentence in cta_sentences}
        sentences = [sentence for sentence in sentences
                     if not any(term in sentence.lower() for term in ("book a walkthrough", "7-day trial", "7 day trial", "learn more"))
                     and re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip() not in cta_keys]
        base = ". ".join(sentences).strip()
        setattr(draft.platform_copy, field, (base + ". " if base else "") + cta_registry[draft.objective]["copy"])
    draft.slides = ordered
    return draft


def normalize_writer_payload(payload: dict) -> tuple[dict, list[str]]:
    """Reduce a seven-slide model payload to the canonical six-slide schema.

    This is a structural operation only. It never invents or edits claims.
    Other schema violations are left for the bounded schema-repair stage.
    """
    slides = payload.get("slides")
    if not isinstance(slides, list) or len(slides) != 7:
        return payload, []

    desired = ["cover", "evidence", "operational_complexity", "failure_point",
               "control_system", "outcome_cta"]
    selected = []
    for purpose in desired:
        candidates = [slide for slide in slides if isinstance(slide, dict) and slide.get("purpose") == purpose]
        if not candidates:
            return payload, []
        selected.append(candidates[-1] if purpose in {"control_system", "outcome_cta"} else candidates[0])

    normalized = dict(payload)
    normalized["slides"] = [dict(slide, number=index) for index, slide in enumerate(selected, 1)]
    return normalized, ["trimmed_writer_slides_7_to_6"]


def closure_normalize(draft: DraftCampaign, cta_registry: dict, product_claims: dict,
                      prohibited: dict) -> tuple[DraftCampaign, list[str]]:
    """Apply only deterministic, source-backed closure edits.

    Creative choices remain model-owned. Python owns offer policy, product wording,
    known-safe operational observations and platform copy grounding.
    """
    draft = normalize_story(draft, cta_registry)
    actions: list[str] = []
    approved_products = {item["id"]: item["text"] for item in product_claims["approved"]
                         if item["id"].startswith("product.")}
    forbidden_terms = [item.lower() for item in prohibited.get("case_insensitive_terms", [])]

    def unsafe(text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in forbidden_terms) or any(
            phrase in lowered for phrase in GENERIC_COPY
        )

    # Product references before the control slide break the story and can also
    # smuggle unsupported product language into an otherwise safe campaign.
    for slide in draft.slides[:-2]:
        text = f"{slide.headline} {slide.body}"
        product_early = BRAND_NAME.lower() in text.lower() or any(
            claim_id.startswith("product.") for claim_id in slide.claim_ids
        )
        ungrounded = not slide.claim_ids and bool(CLAIM_LANGUAGE.search(text))
        words = re.findall(r"[A-Za-z0-9]+", slide.headline)
        malformed = not 2 <= len(words) <= 9 or slide.headline != slide.headline.upper()
        if product_early or ungrounded or unsafe(text) or malformed:
            headline, body, visual, queries = SAFE_SLIDE_COPY[slide.purpose]
            slide.headline, slide.body = headline, body
            slide.visual_concept, slide.image_queries = visual, list(queries)
            slide.claim_ids = []
            actions.append(f"safe_copy:{slide.purpose}")
        elif "?" in slide.headline and slide.purpose != "failure_point":
            slide.headline = SAFE_SLIDE_COPY[slide.purpose][0]
            actions.append(f"safe_headline:{slide.purpose}")

        if slide.asset_strategy not in {"product_ui", "branded_end_card"}:
            asset_text = " ".join([slide.visual_concept, *slide.image_queries]).lower()
            invalid_query = not slide.image_queries or any(
                ABSTRACT_IMAGE_LANGUAGE.search(query) or re.search(r"https?://|\bwww\.", query, re.I)
                for query in slide.image_queries
            ) or any(term in asset_text for term in forbidden_terms)
            if invalid_query:
                slide.visual_concept = SAFE_SLIDE_COPY[slide.purpose][2]
                slide.image_queries = list(SAFE_SLIDE_COPY[slide.purpose][3])
                actions.append(f"safe_image_query:{slide.purpose}")

    control = draft.slides[-2]
    priority = [
        "product.control_layer", "product.document_upload", "product.document_validation",
        "product.internal_dashboard", "product.client_dashboard", "product.milestones",
        "product.audit_trail", "product.digital_archive", "product.web_based",
    ]
    declared = set(draft.claim_ids) | set(control.claim_ids)
    chosen = next((claim_id for claim_id in priority if claim_id in declared and claim_id in approved_products),
                  "product.control_layer")
    expected_headline = CONTROL_HEADLINES[chosen]
    expected_body = approved_products[chosen]
    expected_visual = f"Approved {BRAND_NAME} product interface showing the cargo record and its operational details."
    if (control.headline != expected_headline or control.body != expected_body or
            control.claim_ids != [chosen] or control.image_queries or control.visual_concept != expected_visual):
        control.headline = expected_headline
        control.body = expected_body
        control.claim_ids = [chosen]
        control.visual_concept = expected_visual
        control.image_queries = []
        actions.append("grounded_control_system")

    cta = draft.slides[-1]
    expected_cta_visual = f"Official {BRAND_NAME} branded end card with the approved call to action."
    if cta.visual_concept != expected_cta_visual or cta.image_queries:
        cta.visual_concept = expected_cta_visual
        cta.image_queries = []
        actions.append("approved_end_card")

    lead = SAFE_NARRATIVE_LEADS.get(
        draft.narrative, "Freight teams work around shared cargo records and operational handoffs."
    )
    product_sentence = CONTROL_CAPTIONS[chosen]
    caption = f"{lead} {product_sentence} {cta_registry[draft.objective]['copy']}"
    if (draft.platform_copy.instagram_caption != caption or draft.platform_copy.x_post != caption):
        draft.platform_copy.instagram_caption = caption
        draft.platform_copy.x_post = caption[:280]
        actions.append("grounded_platform_copy")

    used_claims = []
    for slide in draft.slides:
        for claim_id in slide.claim_ids:
            if not claim_id.startswith("offer.") and claim_id not in used_claims:
                used_claims.append(claim_id)
    if draft.claim_ids != used_claims:
        draft.claim_ids = used_claims
        actions.append("pruned_unused_claim_ids")

    return draft, list(dict.fromkeys(actions))
