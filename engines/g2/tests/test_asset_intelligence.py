import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from g2_runtime.assets import build_asset_plan, rank_candidates, validate_reference_registry
from g2_runtime.ingest import load_campaign
from g2_runtime.models import AssetCandidate, AssetRecord
from g2_runtime.omniroute_image import OmniRouteImageProvider
from g2_runtime.policy import validate_asset
from g2_runtime.prompts import image_prompt, validate_background_prompt
from g2_runtime.resolver import AssetResolver, download_stock

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "fixtures/campaign_ready.json"
REFERENCE_ROOT = ROOT / "assets/references"
REGISTRY = REFERENCE_ROOT / "reference_registry.json"


def image_bytes(width=800, height=1000, seed=0):
    image = Image.new("RGB", (width, height), "#091426")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = int(255 * y / height)
        draw.line((0, y, width, y), fill=(shade // 4, shade // 2, shade))
    x = 40 + (seed * 173) % 560
    draw.rectangle((x, 160 + seed * 40, min(x + 180, width - 10), 780 - seed * 30), fill=(220, 60 + seed * 30, 100 + seed * 20))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, payload, url="https://images.pexels.com/photos/test.jpeg", content_type="application/json"):
        self.payload = payload
        self.url = url
        self.headers = {"Content-Type": content_type}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return self.payload
    def geturl(self): return self.url


class FailingStock:
    def search(self, query, limit):
        raise RuntimeError("provider unavailable")


class AssetIntelligenceTests(unittest.TestCase):
    def test_reference_integrity_and_no_reuse(self):
        result = validate_reference_registry(REFERENCE_ROOT, REGISTRY)
        self.assertEqual(len(result["references"]), 5)
        self.assertFalse(result["reuse_as_output"])

    def test_plan_has_no_generated_image_fallback(self):
        plan = build_asset_plan(load_campaign(CAMPAIGN))
        self.assertEqual(plan.policy, "explicit_media_with_control_illustration")
        self.assertEqual(plan.items[0].providers, ["pexels", "pixabay"])
        self.assertIsNone(plan.items[0].generation_prompt)
        self.assertFalse(plan.items[4].requires_owned_asset)
        self.assertEqual(plan.items[4].providers, ["owned"])
        self.assertEqual(plan.items[4].fallback, "deterministic_control_illustration")
        self.assertTrue(plan.items[5].deterministic)

    def test_prompt_is_background_only(self):
        prompt = image_prompt(load_campaign(CAMPAIGN).slides[0])
        validate_background_prompt(prompt)
        self.assertIn("no text", prompt.lower())
        self.assertIn("no logos", prompt.lower())

    def test_ranking_prefers_portrait_resolution(self):
        low = AssetCandidate(candidate_id="pixabay:1", provider="pixabay", source_type="stock", query="freight operator", description="freight", license="x", width=640, height=480)
        high = AssetCandidate(candidate_id="pexels:2", provider="pexels", source_type="stock", query="freight operator", description="freight operator", license="x", width=1400, height=1800)
        self.assertEqual(rank_candidates([low, high], "freight operator")[0].candidate_id, "pexels:2")

    def test_stock_url_allowlist(self):
        candidate = AssetCandidate(candidate_id="pexels:1", provider="pexels", source_type="stock", query="x", license="x", width=1000, height=1400, download_url="https://127.0.0.1/private.jpg")
        with self.assertRaises(ValueError):
            download_stock(candidate)

    def test_omniroute_base64_contract(self):
        body = json.dumps({"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}).encode()
        provider = OmniRouteImageProvider(base_url="http://127.0.0.1:20128/v1", api_key="test", model="image-test", opener=lambda request, timeout: FakeResponse(body))
        raw, metadata = provider.generate("safe prompt")
        self.assertGreater(len(raw), 100)
        self.assertEqual(metadata["model"], "image-test")

    def test_resolver_reports_unresolved_instead_of_generating_fallback(self):
        campaign = load_campaign(CAMPAIGN)
        with tempfile.TemporaryDirectory() as temp:
            result = AssetResolver(stock_providers=[FailingStock()]).resolve(campaign, temp, REFERENCE_ROOT, REGISTRY)
            self.assertEqual(result["assets"], [])
            unresolved = [item for item in result["resolution"] if item["status"] == "unresolved"]
            self.assertEqual(len(unresolved), 4)
            control = [item for item in result["resolution"] if item["status"] == "deterministic_control_illustration"]
            self.assertEqual(len(control), 1)

    def test_unapproved_generated_asset_cannot_render(self):
        campaign = load_campaign(CAMPAIGN)
        record = AssetRecord(slide_number=1, local_path="references/reference_01_corridor_collage.jpg", provider="owned", source_type="generated", license="test", approved=False)
        with self.assertRaises(ValueError):
            validate_asset(record, ROOT / "assets", campaign)


if __name__ == "__main__":
    unittest.main()
