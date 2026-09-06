import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from g2_runtime.catalog import LocalCatalogProvider, index_approved_media
from g2_runtime.ingest import load_campaign
from g2_runtime.media_intelligence import (
    FederatedMediaSearch,
    build_media_search_plan,
    score_media_candidate,
)
from g2_runtime.mixed_video import _render_scene, load_asset_records, render_video
from g2_runtime.models import AssetCandidate
from g2_runtime.models import AssetRecord
from g2_runtime.storyboard import compile_storyboard
from g2_runtime.video_layers import render_layers


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "fixtures" / "campaign_ready.json"
OUTRO = ROOT / "assets" / "references" / "reference_05_cargo_trail.jpg"
SHOWCASE = ROOT / "assets" / "references" / "reference_05_cargo_trail.jpg"
ASSET_ROOT = ROOT / "assets" / "demo"
MANIFEST = ASSET_ROOT / "asset_manifest.json"


def candidate(identifier="clip-1", media_type="video", duration=12.0, description="freight operator cargo documents"):
    return AssetCandidate(
        candidate_id=identifier, provider="local", source_type="owned",
        media_type=media_type, query="freight operator cargo documents",
        description=description, license="owned", width=1080, height=1920,
        duration_seconds=duration if media_type == "video" else None,
    )


class StaticProvider:
    def __init__(self, values):
        self.values = values

    def search(self, query, limit=5, media_type="image"):
        return [item for item in self.values if item.media_type == media_type][:limit]


class BrokenProvider:
    def search(self, query, limit=5, media_type="image"):
        raise RuntimeError("provider unavailable")


class MediaIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.campaign = load_campaign(CAMPAIGN)
        self.storyboard = compile_storyboard(self.campaign, 45)
        self.plan = build_media_search_plan(self.campaign, self.storyboard)

    def test_plan_is_timed_and_routes_media_by_purpose(self):
        self.assertAlmostEqual(self.plan.requirements[-1].end_seconds, 45.0, places=2)
        self.assertEqual(self.plan.requirements[0].preferred_media_type, "video")
        self.assertEqual(self.plan.requirements[-2].allowed_media_types, ["lottie", "svg"])
        self.assertEqual(self.plan.requirements[-1].selection_rule, "approved premade branded outro only")

    def test_literal_queries_remove_abstract_failure_language(self):
        queries = " ".join(value for requirement in self.plan.requirements for value in requirement.queries).lower()
        self.assertNotIn("uncertainty", queries)
        self.assertNotIn("outdated", queries)

    def test_ranking_prefers_timed_portrait_video(self):
        requirement = self.plan.requirements[0]
        video = score_media_candidate(candidate(), requirement)
        still = score_media_candidate(candidate("still", "image"), requirement)
        short = score_media_candidate(candidate("short", duration=1.0), requirement)
        self.assertGreater(video.score, still.score)
        self.assertGreater(video.score, short.score)

    def test_generic_office_clip_cannot_represent_document_evidence(self):
        requirement = self.plan.requirements[1]
        coffee = candidate(
            "coffee", duration=10.0,
            description="coffee coffee mug office desk morning workspace",
        )
        ranked = score_media_candidate(coffee, requirement)
        self.assertEqual(ranked.score, 0.0)
        self.assertIn("required visual anchor missing", ranked.score_reasons[0])

    def test_document_anchor_survives_the_visual_gate(self):
        requirement = self.plan.requirements[1]
        ranked = score_media_candidate(candidate(description="office desk with shipping documents"), requirement)
        self.assertGreater(ranked.score, 0.0)
        self.assertTrue(any("anchor matched" in reason for reason in ranked.score_reasons))

    def test_vector_animation_is_not_penalized_for_its_design_canvas(self):
        requirement = self.plan.requirements[-2]
        vector = AssetCandidate(
            candidate_id="workflow", provider="lordicon", source_type="vector",
            media_type="lottie", query="connected workflow", description="connected workflow",
            license="free", width=512, height=512,
        )
        ranked = score_media_candidate(vector, requirement)
        self.assertGreaterEqual(ranked.score, 0.34)

    def test_federated_search_fails_soft_and_does_not_reuse_one_clip(self):
        value = FederatedMediaSearch([BrokenProvider(), StaticProvider([candidate()])], max_workers=3).search(self.plan)
        selected = [scene["selected"] for scene in value["scenes"] if scene["selected"]]
        self.assertLessEqual(len(selected), 1)
        self.assertTrue(value["errors"])
        self.assertFalse(value["publish_allowed"])

    def test_local_catalog_can_precede_network_search(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.json"
            path.write_text(json.dumps([candidate().model_dump(mode="json")]), encoding="utf-8")
            results = LocalCatalogProvider(path).search("freight cargo documents", media_type="video")
        self.assertEqual(results[0].candidate_id, "clip-1")

    def test_only_approved_media_enters_local_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "approved.jpg").write_bytes(b"approved")
            records = [
                AssetRecord(slide_number=1, local_path="approved.jpg", approved=True, sha256="abc", width=1080, height=1920),
                AssetRecord(slide_number=2, local_path="ignored.jpg", approved=False, width=1080, height=1920),
            ]
            result = index_approved_media(records, self.campaign, root, root / "catalog.json")
            catalog = json.loads((root / "catalog.json").read_text())
        self.assertEqual(result["total"], 1)
        self.assertEqual(catalog["candidates"][0]["provider"], "local")

    def test_video_layer_preserves_source_and_render_uses_direct_clip(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            source.touch()
            layers = render_layers(self.storyboard.scenes[0], source, OUTRO, temp, review=True)
            self.assertEqual(layers["background_kind"], "video")
            self.assertIsNone(layers["preview"])
            layers["clip_start_seconds"] = 1.25
            with patch("g2_runtime.mixed_video._run") as run:
                _render_scene(self.storyboard.scenes[0], layers, Path("audio.wav"), Path("scene.mp4"), 1080, 1920, 30)
            command = " ".join(run.call_args.args[0])
            self.assertIn("-stream_loop -1", command)
            self.assertIn("-ss 1.250", command)
            self.assertIn("force_original_aspect_ratio=increase", command)
            self.assertNotIn("-tune stillimage", command)

    def test_vector_candidate_is_never_opened_as_a_bitmap(self):
        records = load_asset_records(MANIFEST) + [AssetRecord(
            slide_number=5, local_path="workflow.json", approved=False,
            media_type="lottie", provider="lordicon", source_type="illustration",
        )]
        calls = []

        def fake_layers(scene, asset, outro, output, review, size, showcase_path=None):
            calls.append((scene.number, asset))
            return {"background": "unused.png", "background_kind": "image", "overlay": "unused.png", "preview": None}

        def fake_concat(files, destination):
            destination.touch()

        with tempfile.TemporaryDirectory() as temp, \
                patch("g2_runtime.mixed_video.render_layers", side_effect=fake_layers), \
                patch("g2_runtime.mixed_video._render_scene"), \
                patch("g2_runtime.mixed_video._concat", side_effect=fake_concat), \
                patch("g2_runtime.mixed_video.silence"), \
                patch("g2_runtime.mixed_video.probe_duration", return_value=45.0):
            result = render_video(
                self.campaign, self.storyboard, records, ASSET_ROOT, SHOWCASE, OUTRO, temp,
                review=True, voice_mode="silent", subtitle_engine="none",
            )
        self.assertIsNone(dict(calls)[5])
        vector = next(item for item in result["asset_provenance"] if item["scene"] == 5)
        self.assertFalse(vector["used"])


if __name__ == "__main__":
    unittest.main()
