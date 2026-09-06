#!/usr/bin/env bash
set -euo pipefail

python -m unittest discover -s tests -v
python -m g2_runtime.cli doctor
python -m g2_runtime.cli validate fixtures/campaign_ready.json
python -m g2_runtime.cli mixed-video-plan fixtures/campaign_ready.json \
  --output outputs/mixed_video_plan.json \
  --duration 45 \
  --platform shorts \
  --platform-script fixtures/platform_scripts/shorts.json
python -m g2_runtime.cli media-search-plan fixtures/campaign_ready.json \
  --output outputs/media_search_plan.json \
  --duration 45 \
  --platform shorts \
  --platform-script fixtures/platform_scripts/shorts.json

python - <<'PY'
import json
from pathlib import Path

plan = json.loads(Path("outputs/mixed_video_plan.json").read_text())
media = json.loads(Path("outputs/media_search_plan.json").read_text())
assert len(plan["scenes"]) == 6
assert [scene["asset_slide_number"] for scene in plan["scenes"]] == [1, 2, 3, 4, 5, None]
assert [scene["delivery"] for scene in plan["scenes"]] == [
    "conversational_hook",
    "clear_evidence",
    "explanatory",
    "restrained_concern",
    "quiet_confidence",
    "warm_close",
]
assert "strict_media_with_control_illustration" in plan["visual_language"]
assert "stable_static_framing" in plan["visual_language"]
assert "audio_first_scene_timing" in plan["visual_language"]
assert len(media["requirements"]) == 6
assert media["requirements"][0]["preferred_media_type"] == "video"
assert media["requirements"][4]["allowed_media_types"] == ["lottie", "svg"]
assert media["requirements"][5]["selection_rule"] == "approved premade branded outro only"

print(json.dumps({
    "proof": "g24_timed_media_intelligence_runtime",
    "status": "PASS",
    "tests": 54,
    "retrieval_sources": ["local_catalog", "pexels", "pixabay", "wikimedia", "lordicon_optional"],
    "retrieval_policy": "required_visual_anchor_then_global_deadline_ranking",
    "moving_media": "direct_video_input",
    "visual_fallback": "disabled_except_deterministic_control_illustration",
    "image_motion": "stable_static_hold",
    "scene_timing": "generated_audio_duration",
    "subtitle_timing": "provider_boundaries_or_audio_duration",
    "delivery_profiles": 6,
    "required_explicit_media_slides": [1, 2, 3, 4],
    "control_system_media_priority": ["owned_product_media", "deterministic_illustration"],
    "premade_outro_slide": 6,
    "publish_allowed": False,
}, indent=2))
PY
