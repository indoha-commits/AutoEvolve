import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import fastapi  # noqa: F401
except ImportError:
    fastapi_module = types.ModuleType("fastapi")

    class APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda function: function

        post = get

    class HTTPException(Exception):
        pass

    responses_module = types.ModuleType("fastapi.responses")
    responses_module.FileResponse = object
    fastapi_module.APIRouter = APIRouter
    fastapi_module.Body = lambda default=None, **_kwargs: default
    fastapi_module.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.responses"] = responses_module

if "dotenv" not in sys.modules:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.dotenv_values = lambda _path: {}
        sys.modules["dotenv"] = dotenv_module

from app.marketing_api import (
    _artifact_summary,
    _progress,
    _safe_json,
    approve_script,
    regenerate_script,
    retry_campaign,
)
import core.marketing_store as marketing_store
import core.state as state


class MarketingLiveUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "company.db"
        self.old_state_path = state.DB_PATH
        self.old_marketing_path = marketing_store.DB_PATH
        state.DB_PATH = self.database
        marketing_store.DB_PATH = self.database
        state.init_db()
        marketing_store.init_marketing_db()
        self.project = state.create_project("Example Company", "company-core")
        self.task = state.create_task(
            project_id=self.project["id"], agent="growth", task_type="campaign", input_text="Create campaign",
        )

    def tearDown(self):
        state.DB_PATH = self.old_state_path
        marketing_store.DB_PATH = self.old_marketing_path
        self.temporary.cleanup()

    def campaign(self):
        return marketing_store.create_campaign(
            project_id=self.project["id"], task_id=self.task["id"], request="Create campaign",
            objective="awareness", buyer="ops_manager", topic="document handoffs",
            social_platforms=["instagram", "x"], video_platform="shorts",
        )

    def test_artifacts_appear_as_each_file_becomes_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "projects"
            campaign_root = root / "company-core" / "marketing" / "campaigns" / "mkt_1"
            campaign_root.mkdir(parents=True)
            package = campaign_root / "g1_campaign.json"
            package.write_text(json.dumps({
                "title": "One current record",
                "status": "ready_for_media",
                "scenes": [
                    {"scene": 1, "headline": "The wrong version", "body": "A shipment keeps moving."},
                    {"scene": 2, "headline": "One record", "body": "Connect every handoff."},
                ],
                "platform_copy": {"instagram_caption": "Keep one current record."},
            }))
            campaign = {
                "g1_output_path": str(package),
                "media_search_path": None,
                "asset_manifest_path": None,
                "g3_result": None,
            }
            with patch.dict("os.environ", {"MARKETING_CTA_URL": "https://example.com/quiz", "MARKETING_POPUP_OFFER": "ops checklist"}, clear=False):
                with patch("app.marketing_api.PROJECTS_ROOT", root.resolve()):
                    artifacts = _artifact_summary(campaign)
            self.assertEqual(artifacts["script"]["title"], "One current record")
            self.assertEqual(len(artifacts["script"]["scenes"]), 2)
            self.assertIn("A shipment keeps moving", artifacts["script"]["transcript"])
            variants = artifacts["script"]["short_caption_variants"]
            self.assertIn("instagram", variants)
            self.assertIn("Comment QUIZ", artifacts["script"]["platform_copy"]["instagram_caption"])
            self.assertIn("https://example.com/quiz", artifacts["script"]["platform_copy"]["instagram_caption"])
            self.assertTrue(all(len(item.splitlines()) <= 3 for item in variants["instagram"]))
            self.assertTrue(all(any(keyword in item for keyword in ("QUIZ", "DEMO", "FLOW", "CHECKLIST")) for item in variants["instagram"]))
            self.assertNotIn("media", artifacts)

    def test_artifact_reader_rejects_paths_outside_campaign_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "projects"
            root.mkdir()
            outside = Path(temporary) / "secret.json"
            outside.write_text('{"secret": true}')
            with patch("app.marketing_api.PROJECTS_ROOT", root.resolve()):
                self.assertIsNone(_safe_json(str(outside)))

    def test_variant_progress_uses_persisted_queue(self):
        campaign = {
            "current_stage": "variant_rendering",
            "status": "running",
            "variants": [
                {"status": "ready"},
                {"status": "failed"},
                {"status": "rendering"},
                {"status": "queued"},
            ],
        }
        self.assertEqual(_progress(campaign)["percent"], 73)

    def test_founder_approval_resumes_at_media_without_regenerating_g1(self):
        campaign = self.campaign()
        marketing_store.update_campaign(
            campaign["id"], status="needs_campaign_review", current_stage="g1_review",
            g1_output_path="/tmp/g1_campaign.json",
        )
        with patch("app.marketing_api.spawn") as spawn:
            result = approve_script(campaign["id"])
        stored = marketing_store.get_campaign(campaign["id"])
        self.assertEqual(result["next_stage"], "media_search")
        self.assertEqual(stored["current_stage"], "media_queued")
        self.assertTrue(stored["g1_approved_at"])
        spawn.assert_called_once_with(campaign["id"], "media")

    def test_regeneration_requires_direction_and_clears_old_variants(self):
        campaign = self.campaign()
        marketing_store.update_campaign(
            campaign["id"], status="needs_campaign_review", current_stage="g1_review",
            g1_output_path="/tmp/g1_campaign.json",
        )
        marketing_store.create_variant(campaign["id"], "shorts", "Voice-A", 1.0)
        with patch("app.marketing_api.spawn") as spawn:
            regenerate_script(campaign["id"], {"direction": "Use a customs handoff perspective with a new opening."})
        stored = marketing_store.get_campaign(campaign["id"])
        self.assertEqual(stored["current_stage"], "g1_queued")
        self.assertEqual(stored["variants"], [])
        self.assertIn("customs handoff", stored["g1_revision_instruction"])
        spawn.assert_called_once_with(campaign["id"], "pipeline")

    def test_technical_retry_resumes_from_media_stage(self):
        campaign = self.campaign()
        marketing_store.update_campaign(
            campaign["id"], status="failed", current_stage="media_search",
            g1_output_path="/tmp/g1_campaign.json",
        )
        with patch("app.marketing_api.spawn") as spawn:
            result = retry_campaign(campaign["id"])
        self.assertEqual(result["resume_stage"], "media")
        spawn.assert_called_once_with(campaign["id"], "media")


if __name__ == "__main__":
    unittest.main()
