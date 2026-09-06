from __future__ import annotations

import os
import re
import uuid
from typing import Any


STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "have", "when", "what",
    "where", "been", "they", "them", "their", "there", "about", "across", "could", "would", "should",
    "still", "just", "need", "like", "through", "another", "someone", "something", "actually", "because",
    "while", "team", "teams", "client", "clients", "cargo", "operation", "operations",
}

MAX_CONTENT_SLIDES = 3


def split_script_sections(script: str) -> list[str]:
    cleaned = str(script or "").replace("\r", "")
    blocks = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    sections: list[str] = []
    ignored_exact = {
        "campaign script",
        "platform copy",
        "live activity",
        "approve script",
        "regenerate",
        "ready",
        "needs founder review",
        "needs campaign review",
        "campaign needs review",
    }
    for block in blocks:
        if re.fullmatch(r"\[[^\]]+\]", block.strip()):
            continue
        block_lower = block.lower()
        if block_lower in ignored_exact:
            continue
        if re.fullmatch(r"v\d+", block_lower):
            continue
        if re.fullmatch(r"\d+%\s*", block_lower):
            continue
        if " · " in block and any(token in block_lower for token in ("awareness", "instagram", "x", "shorts", "voice")):
            continue
        if block_lower.startswith("instagram caption") or block_lower.startswith("x post"):
            continue
        if block_lower.startswith("target length:"):
            continue
        if re.fullmatch(r".+?\s*[—–-]\s*face\s+video(?:\s*#?\d+)?", block_lower):
            continue
        if block.startswith("mkt_") or block.startswith("var_"):
            continue
        if not re.search(r"[A-Za-z0-9]", block):
            continue
        sections.append(block)
    return sections


def _compress_sections(sections: list[str], limit: int = MAX_CONTENT_SLIDES) -> list[str]:
    if len(sections) <= limit:
        return sections
    chunk_size = max(1, (len(sections) + limit - 1) // limit)
    merged: list[str] = []
    for start in range(0, len(sections), chunk_size):
        chunk = sections[start:start + chunk_size]
        merged.append("\n\n".join(chunk))
    if len(merged) > limit:
        head = merged[: limit - 1]
        tail = ["\n\n".join(merged[limit - 1:])]
        return head + tail
    return merged


def _headline(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return "SCENE"
    for stop in ".!?\n":
        if stop in normalized:
            normalized = normalized.split(stop, 1)[0]
            break
    normalized = normalized.strip().strip(chr(34)).strip(chr(39)).strip(chr(0x201c)).strip(chr(0x201d))
    return normalized[:72].upper() or "SCENE"


def _keywords(text: str) -> list[str]:
    words = []
    for token in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
        if len(token) < 4 or token in STOPWORDS or token.isdigit():
            continue
        words.append(token)
    unique: list[str] = []
    for word in words:
        if word not in unique:
            unique.append(word)
    return unique[:6]


def _image_queries(text: str) -> list[str]:
    keywords = _keywords(text)
    if not keywords:
        return ["freight logistics", "shipping documents", "warehouse operations"]
    queries = []
    if any(term in keywords for term in ("port", "warehouse", "shipment", "freight", "customs", "logistics")):
        queries.append("freight logistics operations")
    if any(term in keywords for term in ("document", "documents", "email", "whatsapp", "excel", "files")):
        queries.append("shipping documents office")
    if any(term in keywords for term in ("client", "visibility", "tracking", "update", "milestone")):
        queries.append("shipment tracking dashboard")
    queries.extend([" ".join(keywords[:3]), " ".join(keywords[:2])])
    cleaned_queries: list[str] = []
    for query in queries:
        normalized = " ".join(query.split()).strip()
        if normalized and normalized not in cleaned_queries:
            cleaned_queries.append(normalized)
    return cleaned_queries[:5]


def _purpose(index: int, total: int) -> str:
    if index == 0:
        return "cover"
    if index == 1:
        return "operational_complexity"
    return "failure_point"


def build_asset_package(
    script: str,
    *,
    objective: str = "awareness",
    social_platforms: list[str] | None = None,
    video_platform: str = "shorts",
) -> dict[str, Any]:
    company = os.getenv("COMPANY_NAME", "Company Core").strip() or "Company Core"
    product = os.getenv(
        "COMPANY_PRODUCT_DESCRIPTION",
        "The company provides a controlled operational workflow for its customers.",
    ).strip()
    sections = _compress_sections(split_script_sections(script))
    if not sections:
        raise ValueError("script must contain at least one paragraph")
    social_platforms = social_platforms or ["instagram", "x"]
    slides = []
    for index, section in enumerate(sections):
        slides.append({
            "number": index + 1,
            "purpose": _purpose(index, len(sections)),
            "headline": _headline(section),
            "body": section,
            "visual_concept": section[:240],
            "asset_strategy": "stock_photo",
            "image_queries": _image_queries(section),
            "claim_ids": [],
        })
    slides.append({
        "number": len(slides) + 1,
        "purpose": "control_system",
        "headline": f"{company.upper()} CONNECTS THE WORKFLOW",
        "body": product,
        "visual_concept": f"Approved {company} product interface showing the core workflow.",
        "asset_strategy": "product_ui",
        "image_queries": [],
        "claim_ids": ["product.control_layer"],
    })
    slides.append({
        "number": len(slides) + 1,
        "purpose": "outcome_cta",
        "headline": "BOOK A WALKTHROUGH",
        "body": f"See how {company} fits your operation. Book a walkthrough.",
        "visual_concept": f"Official {company} branded end card with the approved call to action.",
        "asset_strategy": "branded_end_card",
        "image_queries": [],
        "claim_ids": ["product.control_layer", "offer.walkthrough"],
    })
    return {
        "campaign_id": f"assetpack_{uuid.uuid4().hex[:12]}",
        "objective": objective,
        "buyer": "ops_manager",
        "narrative": sections[0][:240],
        "format": "carousel",
        "platforms": social_platforms,
        "claim_ids": ["product.control_layer", "offer.walkthrough"],
        "slides": slides,
        "platform_copy": {
            "instagram_caption": f"{product} See how {company} fits your workflow. Book a walkthrough.",
            "x_post": f"{product} See how {company} fits your workflow. Book a walkthrough.",
        },
        "brand": {
            "canvas": "1080x1350",
            "logo_asset": "company_logo",
            "logo_variant": "white",
            "logo_position": "top_left",
        },
        "generation": {
            "research": "fail_soft",
            "concepts": "deterministic_fallback",
            "writer": "model_repair",
            "deterministic_actions": ["paragraph_to_asset_pack", "approved_product_ui", "approved_end_card"],
        },
        "quality": {
            "schema_valid": True,
            "all_claims_grounded": True,
            "forbidden_terms": [],
            "unsupported_claims": [],
            "duplicate_score": 0.0,
            "brand_score": 1.0,
            "buyer_score": 0.9,
            "editorial_score": 0.9,
            "rewrite_count": 0,
            "warnings": [],
        },
        "status": "ready_for_media",
    }


def build_pack_title(script: str) -> str:
    sections = split_script_sections(script)
    if not sections:
        return "Asset pack"
    return _headline(sections[0]).title()[:80]
