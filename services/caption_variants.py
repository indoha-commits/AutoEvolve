from __future__ import annotations

import os
from typing import Any

CTA_BY_PLATFORM = {
    "instagram": ["DM FLOW", "Reply DEMO", "Comment FLOW", "DM CHECKLIST"],
    "x": ["Reply DEMO", "DM FLOW", "Reply CHECKLIST", "DM OPS"],
    "linkedin": ["Reply DEMO", "DM FLOW", "Comment DEMO", "DM PLAYBOOK"],
}


def _buyer_label(buyer: str | None, topic: str, transcript: str, platform_copy: dict[str, Any]) -> str:
    normalized = str(buyer or '').replace('_', ' ').strip()
    lowered = normalized.lower()
    if normalized and lowered not in {'ai selected', 'ai buyer', 'unknown', 'n/a'}:
        return normalized
    context = ' '.join(
        part for part in [str(topic or '').strip(), str(transcript or '').strip(), ' '.join(str(value or '').strip() for value in platform_copy.values())]
        if part
    ).lower()
    if any(term in context for term in ('freight forward', 'shipment', 'cargo', 'port', 'customs')):
        return 'freight ops teams'
    if any(term in context for term in ('logistics', 'handoff', 'warehouse', 'transport')):
        return 'logistics operators'
    if any(term in context for term in ('documents', 'audit', 'visibility', 'workflow')):
        return 'operations teams'
    return 'ops teams'


def build_short_caption_variants(*, topic: str, buyer: str, transcript: str, platform_copy: dict[str, Any] | None = None) -> dict[str, list[str]]:
    platform_copy = enrich_platform_copy(platform_copy or {})
    seed = _best_seed(topic, transcript, platform_copy)
    buyer_label = _buyer_label(buyer, topic, transcript, platform_copy)
    lines = [
        seed,
        f"Built for {buyer_label} who need cleaner handoffs and visibility.",
    ]
    variants: dict[str, list[str]] = {}
    for platform in _platforms(platform_copy):
        variants[platform] = [_compose_variant(lines, cta, platform=platform) for cta in CTA_BY_PLATFORM.get(platform, CTA_BY_PLATFORM['linkedin'])]
    return variants


def _platforms(platform_copy: dict[str, Any]) -> list[str]:
    inferred = []
    for key in platform_copy:
        lowered = key.lower()
        if 'instagram' in lowered:
            inferred.append('instagram')
        elif lowered.startswith('x') or 'twitter' in lowered or 'x_post' in lowered:
            inferred.append('x')
        elif 'linkedin' in lowered:
            inferred.append('linkedin')
    return list(dict.fromkeys(inferred or ['instagram', 'x']))


def _best_seed(topic: str, transcript: str, platform_copy: dict[str, Any]) -> str:
    candidates = [
        _first_sentence(str(topic or '').strip()),
        _first_sentence(transcript),
    ]
    for value in platform_copy.values():
        text = str(value or '').strip()
        if text:
            candidates.append(_first_sentence(text))
    for text in candidates:
        normalized = _trim_line(text)
        if normalized:
            return normalized
    return 'Operational visibility turns content into pipeline.'


def _first_sentence(text: str) -> str:
    text = ' '.join(str(text or '').split()).strip()
    if not text:
        return ''
    for stop in '.!?\n':
        if stop in text:
            text = text.split(stop, 1)[0]
            break
    return text.strip()


def _trim_line(text: str, limit: int = 84) -> str:
    normalized = ' '.join(str(text or '').split()).strip()
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[: limit - 1].rsplit(' ', 1)[0].strip()
    return f"{clipped}…" if clipped else normalized[:limit]


def _compose_variant(base_lines: list[str], cta: str, *, platform: str) -> str:
    lines = [*base_lines[:2], _platform_cta_line(platform, keyword=cta.split()[-1] if cta else None)]
    return '\n'.join(_trim_line(line) for line in lines[:3])


def enrich_platform_copy(platform_copy: dict[str, Any] | None) -> dict[str, Any]:
    copy = dict(platform_copy or {})
    for key, value in list(copy.items()):
        platform = _platform_for_key(key)
        if not platform:
            continue
        copy[key] = _append_platform_cta(str(value or ''), platform)
    return copy


def _platform_for_key(key: str) -> str | None:
    lowered = str(key or '').lower()
    if 'instagram' in lowered:
        return 'instagram'
    if lowered.startswith('x') or 'twitter' in lowered:
        return 'x'
    if 'linkedin' in lowered:
        return 'linkedin'
    return None


def _cta_url() -> str:
    return str(os.getenv('MARKETING_CTA_URL', '') or '').strip()


def _popup_offer() -> str:
    return str(os.getenv('MARKETING_POPUP_OFFER', '') or '').strip()


def _platform_cta_line(platform: str, *, keyword: str | None = None) -> str:
    keyword = (keyword or 'QUIZ').strip().upper()
    url = _cta_url()
    offer = _popup_offer() or 'ops checklist'
    if platform == 'instagram':
        if url:
            return _trim_line(f'Comment {keyword} or tap the bio link for the {offer}: {url}', 116)
        return _trim_line(f'Comment {keyword} for the {offer}.', 116)
    if url:
        return _trim_line(f'Take the quiz and get the {offer}: {url}', 116)
    return _trim_line(f'Reply {keyword} for the {offer}.', 116)


def _append_platform_cta(text: str, platform: str) -> str:
    base = str(text or '').strip()
    cta = _platform_cta_line(platform)
    lowered = base.lower()
    url = _cta_url().lower()
    if cta.lower() in lowered or (url and url in lowered):
        return base
    if not base:
        return cta
    separator = '\n\n' if '\n' in base else '\n'
    limit = 520 if platform == 'instagram' else 280 if platform == 'x' else 420
    combined = f'{base}{separator}{cta}'.strip()
    if len(combined) <= limit:
        return combined
    allowed = max(40, limit - len(cta) - len(separator))
    clipped = _trim_line(base, allowed)
    return f'{clipped}{separator}{cta}'.strip()
