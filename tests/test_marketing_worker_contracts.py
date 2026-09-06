import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


if "dotenv" not in sys.modules:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        module = types.ModuleType("dotenv")
        module.dotenv_values = lambda _path: {}
        sys.modules["dotenv"] = module

import core.marketing_store as marketing_store
import core.state as state
from services.marketing_worker import (
    _approved_media_package,
    _build_handoff,
    _command_environment,
    _last_json,
    _project_g2_package,
    run_pipeline,
    run_real_voice_render,
)


class MarketingWorkerContractTests(unittest.TestCase):
    def test_g2_projection_removes_narration_aliases_and_uses_voiceover(self):
        original = {
            "campaign_id": "camp_test",
            "objective": "awareness",
            "buyer": "freight ops",
            "narrative": "Cargo visibility",
            "format": "carousel",
            "platforms": ["instagram"],
            "claim_ids": [],
            "transcript": "Full transcript",
            "narration": "Full transcript",
            "script": "Full transcript",
            "slides": [{
                "number": 1,
                "purpose": "cover",
                "headline": "Where is the cargo?",
                "body": "Short card copy",
                "voiceover": "The complete spoken scene.",
                "visual_concept": "Freight operator",
                "asset_strategy": "stock_photo",
                "image_queries": ["freight operator"],
                "claim_ids": [],
            }],
            "platform_copy": {"instagram_caption": "Caption", "x_post": "Post", "extra": True},
            "brand": {"canvas": "1080x1350", "logo_asset": "company_core_logo", "logo_variant": "white", "logo_position": "top_left"},
            "generation": {"research": "r", "concepts": "c", "writer": "w", "deterministic_actions": [], "extra": True},
            "quality": {"schema_valid": True, "all_claims_grounded": True, "forbidden_terms": [], "unsupported_claims": [], "duplicate_score": 0.0, "brand_score": 1.0, "buyer_score": 1.0, "editorial_score": 1.0, "rewrite_count": 0, "warnings": [], "extra": True},
            "status": "ready_for_media",
            "extra": True,
        }

        projected = _project_g2_package(original)

        self.assertNotIn("transcript", projected)
        self.assertNotIn("narration", projected)
        self.assertNotIn("script", projected)
        self.assertNotIn("extra", projected)
        self.assertNotIn("voiceover", projected["slides"][0])
        self.assertEqual(projected["slides"][0]["body"], "The complete spoken scene.")
        self.assertNotIn("extra", projected["platform_copy"])
        self.assertEqual(original["slides"][0]["body"], "Short card copy")

    def test_tool_virtualenv_bin_is_prepended_to_subprocess_path(self):
        command = ["/opt/company-core-g2/.venv/bin/company-core-g2", "--help"]
        with patch.dict("os.environ", {"PATH": "/usr/local/bin:/usr/bin"}, clear=False):
            environment = _command_environment(command, None)
        self.assertEqual(
            environment["PATH"].split(":"),
            ["/opt/company-core-g2/.venv/bin", "/usr/local/bin", "/usr/bin"],
        )

    def test_founder_approval_creates_hash_bound_g2_derivative(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "g1_campaign.json"
            original = {
                "status": "needs_founder_review",
                "quality": {"warnings": ["copy similarity 1.00 exceeds 0.58"]},
                "generation": {"deterministic_actions": []},
            }
            source.write_text(json.dumps(original))
            campaign = {
                "id": "mkt_test",
                "g1_output_path": str(source),
                "g1_approved_at": "2026-08-27T14:00:00+00:00",
            }
            with patch("services.marketing_worker.add_event") as add_event:
                approved_path, approved = _approved_media_package(campaign, original, root)
            self.assertEqual(approved_path.name, "g1_campaign_approved.json")
            self.assertEqual(approved["status"], "ready_for_media")
            self.assertEqual(approved["quality"]["warnings"], [])
            self.assertIn("source_sha256=", approved["generation"]["deterministic_actions"][0])
            self.assertEqual(json.loads(source.read_text()), original)
            add_event.assert_called_once()

    def test_last_json_ignores_logs_and_nested_objects(self):
        text = "[FETCH] ok\n" + json.dumps({"campaign": {"status": "ready"}, "output": "/tmp/a.json"}) + "\n"
        self.assertEqual(_last_json(text)["output"], "/tmp/a.json")

    def test_g3_handoff_is_draft_only_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_path = root / "g1_campaign.json"
            manifest_path = root / "video_render_manifest.json"
            video_path = root / "variants" / "var_1" / "review.mp4"
            video_path.parent.mkdir(parents=True)
            video_path.write_bytes(b"review-video")
            digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
            package_path.write_text(json.dumps({
                "campaign_id": "camp_123",
                "platform_copy": {
                    "instagram_caption": "Instagram caption",
                    "x_post": "X caption",
                },
            }))
            manifest_path.write_text(json.dumps({"source_package_sha256": "b" * 64}))
            campaign = {
                "g1_output_path": str(package_path),
                "social_platforms": ["instagram", "x"],
            }
            variant = {
                "manifest_path": str(manifest_path),
                "video_path": str(video_path),
                "sha256": digest,
            }

            path = _build_handoff(campaign, variant, root)
            handoff = json.loads(path.read_text())
            self.assertIs(handoff["publish_allowed"], False)
            self.assertNotIn("schedule", handoff)
            self.assertNotIn("publish_at", handoff)
            self.assertEqual([item["platform"] for item in handoff["drafts"]], ["instagram", "x"])
            self.assertEqual(handoff["drafts"][0]["media"][0]["sha256"], digest)
            self.assertFalse(Path(handoff["drafts"][0]["media"][0]["path"]).is_absolute())

    def test_pipeline_reaches_variant_review_with_bounded_variants(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            database = base / "company.db"
            old_state_path, old_marketing_path = state.DB_PATH, marketing_store.DB_PATH
            state.DB_PATH = database
            marketing_store.DB_PATH = database
            try:
                state.init_db()
                marketing_store.init_marketing_db()
                project = state.create_project("Example Company", "company-core")
                task = state.create_task(
                    project_id=project["id"], agent="growth", task_type="campaign",
                    input_text="Create campaign",
                )
                campaign = marketing_store.create_campaign(
                    project_id=project["id"], task_id=task["id"], request="Create campaign",
                    objective="awareness", buyer="ops_manager", topic="document handoffs",
                    social_platforms=["instagram", "x"], video_platform="shorts",
                )

                class FakeConfig:
                    g1_root = base
                    g1_bin = Path("/bin/true")
                    g1_env_file = None
                    g2_root = base
                    g2_bin = Path("/bin/true")
                    g2_env_file = None
                    g2_showcase = base / "showcase.jpg"
                    g2_outro = base / "outro.jpg"
                    g3_bin = Path("/bin/true")
                    voices = ("Voice-A", "Voice-B")
                    speed = 1.04
                    media_deadline = 1
                    media_limit = 2
                    stage_timeout = 10

                    @staticmethod
                    def diagnostics():
                        return {name: {"ok": True} for name in ("g1_bin", "g2_bin", "g2_showcase", "g2_outro", "g3_bin")}

                def fake_run(command, **_kwargs):
                    if "create" in command:
                        return json.dumps({"campaign": {
                            "campaign_id": "camp_test", "status": "ready_for_media",
                            "quality": {"warnings": []},
                            "platform_copy": {"instagram_caption": "Caption", "x_post": "Post"},
                            "slides": [],
                        }})
                    if "search-media" in command:
                        path = Path(command[command.index("--output") + 1])
                        path.write_text(json.dumps({"campaign_id": "camp_test", "scenes": []}))
                        return json.dumps({"status": "needs_media_review"})
                    if "acquire-media" in command:
                        root = Path(command[command.index("--output") + 1])
                        root.mkdir(parents=True, exist_ok=True)
                        (root / "asset_manifest.json").write_text("[]")
                        return json.dumps({"status": "needs_founder_media_review"})
                    if "render-mixed-video" in command:
                        root = Path(command[command.index("--output") + 1])
                        root.mkdir(parents=True, exist_ok=True)
                        video = root / "camp_test_review.mp4"
                        video.write_bytes(b"video")
                        manifest = {
                            "video": video.name, "sha256": hashlib.sha256(b"video").hexdigest(),
                            "duration_seconds": 30.0, "source_package_sha256": "c" * 64,
                        }
                        (root / "video_render_manifest.json").write_text(json.dumps(manifest))
                        return json.dumps(manifest)
                    raise AssertionError(command)

                with patch("services.marketing_worker.MarketingConfig.load", return_value=FakeConfig()), \
                     patch("services.marketing_worker._run", side_effect=fake_run), \
                     patch("services.marketing_worker.ROOT", base):
                    run_pipeline(campaign["id"])

                stored = marketing_store.get_campaign(campaign["id"])
                self.assertEqual(stored["status"], "variants_ready")
                self.assertEqual([item["voice"] for item in stored["variants"]], ["Voice-A", "Voice-B"])
                self.assertTrue(all(item["status"] == "ready" for item in stored["variants"]))
            finally:
                state.DB_PATH = old_state_path
                marketing_store.DB_PATH = old_marketing_path

    def test_pipeline_real_voice_creates_handoff_after_media_prep(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            database = base / "company.db"
            old_state_path, old_marketing_path = state.DB_PATH, marketing_store.DB_PATH
            state.DB_PATH = database
            marketing_store.DB_PATH = database
            try:
                state.init_db()
                marketing_store.init_marketing_db()
                project = state.create_project("Example Company", "company-core")
                task = state.create_task(
                    project_id=project["id"], agent="growth", task_type="campaign",
                    input_text="Create campaign",
                )
                campaign = marketing_store.create_campaign(
                    project_id=project["id"], task_id=task["id"], request="Create campaign",
                    objective="awareness", buyer="ops_manager", topic="document handoffs",
                    social_platforms=["instagram", "x"], video_platform="shorts",
                    voice_mode="real_voice", voice_transcript="Line one.\n\nLine two.",
                )

                class FakeConfig:
                    g1_root = base
                    g1_bin = Path("/bin/true")
                    g1_env_file = None
                    g2_root = base
                    g2_bin = Path("/bin/true")
                    g2_env_file = None
                    g2_showcase = base / "showcase.jpg"
                    g2_outro = base / "outro.jpg"
                    g3_bin = Path("/bin/true")
                    voices = ("Voice-A", "Voice-B")
                    speed = 1.04
                    media_deadline = 1
                    media_limit = 2
                    stage_timeout = 10

                    @staticmethod
                    def diagnostics():
                        return {name: {"ok": True} for name in ("g1_bin", "g2_bin", "g2_showcase", "g2_outro", "g3_bin")}

                def fake_run(command, **_kwargs):
                    if "create" in command:
                        return json.dumps({"campaign": {
                            "campaign_id": "camp_test", "status": "ready_for_media",
                            "quality": {"warnings": []},
                            "platform_copy": {"instagram_caption": "Caption", "x_post": "Post"},
                            "slides": [
                                {"number": 1, "headline": "One", "body": "Body one"},
                                {"number": 2, "headline": "Two", "body": "Body two"},
                            ],
                        }})
                    if "search-media" in command:
                        path = Path(command[command.index("--output") + 1])
                        path.write_text(json.dumps({"campaign_id": "camp_test", "scenes": []}))
                        return json.dumps({"status": "needs_media_review"})
                    if "acquire-media" in command:
                        root = Path(command[command.index("--output") + 1])
                        root.mkdir(parents=True, exist_ok=True)
                        (root / "asset_manifest.json").write_text(json.dumps({"assets": []}))
                        return json.dumps({"status": "needs_founder_media_review"})
                    if "render-mixed-video" in command:
                        raise AssertionError("real_voice pipeline should stop before variant rendering")
                    raise AssertionError(command)

                with patch("services.marketing_worker.MarketingConfig.load", return_value=FakeConfig()), \
                     patch("services.marketing_worker._run", side_effect=fake_run), \
                     patch("services.marketing_worker.ROOT", base):
                    run_pipeline(campaign["id"])

                stored = marketing_store.get_campaign(campaign["id"])
                self.assertEqual(stored["status"], "needs_voice_recording")
                self.assertEqual(stored["current_stage"], "voice_handoff")
                self.assertTrue(stored["voice_handoff_path"])
                self.assertTrue(Path(stored["voice_handoff_path"]).is_file())
                handoff = json.loads(Path(stored["voice_handoff_path"]).read_text())
                self.assertEqual(handoff["voice_mode"], "real_voice")
                self.assertEqual(handoff["transcript"], "Line one.\n\nLine two.")
                self.assertEqual(len(handoff["scenes"]), 2)
            finally:
                state.DB_PATH = old_state_path
                marketing_store.DB_PATH = old_marketing_path

    def test_real_voice_render_uses_uploaded_audio_and_marks_variant_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            database = base / "company.db"
            old_state_path, old_marketing_path = state.DB_PATH, marketing_store.DB_PATH
            state.DB_PATH = database
            marketing_store.DB_PATH = database
            try:
                state.init_db()
                marketing_store.init_marketing_db()
                project = state.create_project("Example Company", "company-core")
                task = state.create_task(
                    project_id=project["id"], agent="growth", task_type="campaign",
                    input_text="Create campaign",
                )
                campaign = marketing_store.create_campaign(
                    project_id=project["id"], task_id=task["id"], request="Create campaign",
                    objective="awareness", buyer="ops_manager", topic="document handoffs",
                    social_platforms=["instagram", "x"], video_platform="shorts",
                    voice_mode="real_voice", voice_transcript="Line one.\n\nLine two.",
                )
                campaign_root = base / "projects" / project["slug"] / "marketing" / "campaigns" / campaign["id"]
                campaign_root.mkdir(parents=True, exist_ok=True)
                package_path = campaign_root / "g1_campaign_approved.json"
                package_path.write_text(json.dumps({
                    "campaign_id": "camp_test",
                    "slides": [
                        {"number": 1, "headline": "One", "body": "Body one", "voiceover": "Line one."},
                        {"number": 2, "headline": "Two", "body": "Body two", "voiceover": "Line two."},
                    ],
                    "platform_copy": {"instagram_caption": "Caption", "x_post": "Post"},
                }))
                asset_root = campaign_root / "assets"
                asset_root.mkdir(parents=True, exist_ok=True)
                manifest_path = asset_root / "asset_manifest.json"
                manifest_path.write_text(json.dumps({"assets": []}))
                audio_path = campaign_root / "voice" / "narration.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"fake-audio")
                marketing_store.update_campaign(
                    campaign["id"],
                    g1_output_path=str(package_path),
                    asset_root=str(asset_root),
                    asset_manifest_path=str(manifest_path),
                    voice_recording_path=str(audio_path),
                    status="queued",
                    current_stage="voice_render_queued",
                )

                class FakeConfig:
                    g2_root = base
                    g2_bin = Path("/bin/true")
                    g2_env_file = None
                    g2_showcase = base / "showcase.jpg"
                    g2_outro = base / "outro.jpg"
                    stage_timeout = 10

                def fake_run(command, **_kwargs):
                    self.assertIn("--showcase", command)
                    self.assertIn("--voice-mode", command)
                    self.assertIn("silent", command)
                    self.assertIn("--duration", command)
                    root = Path(command[command.index("--output") + 1])
                    root.mkdir(parents=True, exist_ok=True)
                    video = root / "silent_review.mp4"
                    video.write_bytes(b"video")
                    manifest = {
                        "video": video.name,
                        "sha256": hashlib.sha256(b"video").hexdigest(),
                        "duration_seconds": 12.0,
                        "source_package_sha256": "c" * 64,
                        "scenes": [
                            {"number": 1, "duration_seconds": 5.0},
                            {"number": 2, "duration_seconds": 7.0},
                        ],
                    }
                    (root / "video_render_manifest.json").write_text(json.dumps(manifest))
                    return json.dumps(manifest)

                with patch("services.marketing_worker.MarketingConfig.load", return_value=FakeConfig()), \
                     patch("services.marketing_worker._run", side_effect=fake_run), \
                     patch("services.marketing_worker._probe_media_duration", return_value=12.0), \
                     patch("services.marketing_worker._mux_audio_track", side_effect=lambda video, audio, out: out.write_bytes(b"muxed")), \
                     patch("services.marketing_worker.ROOT", base):
                    run_real_voice_render(campaign["id"])

                stored = marketing_store.get_campaign(campaign["id"])
                self.assertEqual(stored["status"], "variants_ready")
                self.assertEqual(stored["current_stage"], "founder_variant_review")
                self.assertEqual(len(stored["variants"]), 1)
                variant = stored["variants"][0]
                self.assertEqual(variant["voice"], "real_voice")
                self.assertEqual(variant["status"], "ready")
                manifest = json.loads(Path(variant["manifest_path"]).read_text())
                self.assertEqual(manifest["voice_mode"], "uploaded_audio")
                self.assertEqual(manifest["uploaded_audio"], "narration.wav")
                self.assertTrue((Path(variant["video_path"]).parent / "captions.srt").is_file())
            finally:
                state.DB_PATH = old_state_path
                marketing_store.DB_PATH = old_marketing_path


if __name__ == "__main__":
    unittest.main()
