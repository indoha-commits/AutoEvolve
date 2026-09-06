import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from g2_runtime.ingest import MediaGateError, load_campaign, package_sha256
from g2_runtime.models import AssetRecord
from g2_runtime.policy import constant_time_secret, validate_asset
from g2_runtime.renderer import render_carousel
from g2_runtime.video import video_manifest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/campaign_ready.json"


def make_logo(path: Path) -> Path:
    image = Image.new("RGBA", (420, 120), "#ffffff")
    image.save(path)
    return path


class RuntimeTests(unittest.TestCase):
    def test_ready_campaign_passes(self):
        self.assertEqual(load_campaign(FIXTURE).status, "ready_for_media")

    def test_review_campaign_is_denied(self):
        data = json.loads(FIXTURE.read_text())
        data["status"] = "needs_founder_review"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(MediaGateError):
                load_campaign(path)

    def test_warnings_are_denied(self):
        data = json.loads(FIXTURE.read_text())
        data["quality"]["warnings"] = ["unsafe"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(MediaGateError):
                load_campaign(path)

    def test_hash_is_stable(self):
        campaign = load_campaign(FIXTURE)
        self.assertEqual(package_sha256(campaign), package_sha256(campaign))

    def test_secret_comparison(self):
        self.assertTrue(constant_time_secret("a", "a"))
        self.assertFalse(constant_time_secret("a", "b"))

    def test_asset_escape_is_denied(self):
        campaign = load_campaign(FIXTURE)
        record = AssetRecord(slide_number=1, local_path="../outside.png")
        with self.assertRaises(ValueError):
            validate_asset(record, ROOT / "assets", campaign)

    def test_unapproved_product_ui_is_denied(self):
        campaign = load_campaign(FIXTURE)
        record = AssetRecord(slide_number=5, local_path="references/reference_03_document_flow.jpg", approved=False)
        with self.assertRaises(ValueError):
            validate_asset(record, ROOT / "assets", campaign)

    def test_approved_control_illustration_is_allowed(self):
        campaign = load_campaign(FIXTURE)
        record = AssetRecord(
            slide_number=5,
            local_path="references/reference_03_document_flow.jpg",
            approved=True,
            source_type="illustration",
        )
        self.assertTrue(validate_asset(record, ROOT / "assets", campaign).is_file())

    def test_video_manifest_preserves_claims(self):
        campaign = load_campaign(FIXTURE)
        manifest = video_manifest(campaign, 45)
        self.assertEqual(sum(item.duration_seconds for item in manifest.scenes), 45)
        self.assertEqual(manifest.scenes[4].claim_ids, ["product.control_layer"])

    def test_proof_renderer_dimensions(self):
        campaign = load_campaign(FIXTURE)
        with tempfile.TemporaryDirectory() as temp:
            logo = make_logo(Path(temp) / "logo.png")
            result = render_carousel(campaign, temp, logo, proof_placeholders=True)
            self.assertEqual(result["status"], "proof_rendered")
            with Image.open(Path(temp) / "slide_01.png") as image:
                self.assertEqual(image.size, (1080, 1350))

    def test_logo_is_present_on_every_slide(self):
        campaign = load_campaign(FIXTURE)
        with tempfile.TemporaryDirectory() as temp:
            logo = make_logo(Path(temp) / "logo.png")
            render_carousel(campaign, temp, logo, proof_placeholders=True)
            for number in range(1, 7):
                with Image.open(Path(temp) / f"slide_{number:02d}.png") as image:
                    crop = image.crop((45, 40, 360, 155)).convert("L")
                    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
                    self.assertGreater(max(pixels), 220, f"logo missing on slide {number}")


if __name__ == "__main__":
    unittest.main()
