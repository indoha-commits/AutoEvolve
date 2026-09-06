import json
import tempfile
import unittest
from pathlib import Path

import core.marketing_store as marketing_store
import core.state as state


class MarketingStoreTests(unittest.TestCase):
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
            project_id=self.project["id"],
            agent="growth",
            task_type="campaign",
            input_text="Create campaign",
        )

    def tearDown(self):
        state.DB_PATH = self.old_state_path
        marketing_store.DB_PATH = self.old_marketing_path
        self.temporary.cleanup()

    def test_campaign_variant_selection_state_is_persistent(self):
        campaign = marketing_store.create_campaign(
            project_id=self.project["id"],
            task_id=self.task["id"],
            request="Create an awareness campaign",
            objective="awareness",
            buyer="ops_manager",
            topic="outdated document versions",
            social_platforms=["instagram", "x"],
            video_platform="shorts",
        )
        variant = marketing_store.create_variant(campaign["id"], "shorts", "en-US-AndrewNeural", 1.04)
        marketing_store.update_variant(
            variant["id"],
            status="ready",
            video_path="/tmp/review.mp4",
            manifest_path="/tmp/video_render_manifest.json",
            sha256="a" * 64,
            duration_seconds=32.4,
        )
        marketing_store.update_campaign(
            campaign["id"],
            status="selected",
            current_stage="founder_draft_approval",
            selected_variant_id=variant["id"],
        )

        stored = marketing_store.get_campaign(campaign["id"])
        self.assertEqual(stored["selected_variant_id"], variant["id"])
        self.assertEqual(stored["social_platforms"], ["instagram", "x"])
        self.assertEqual(stored["variants"][0]["status"], "ready")

    def test_events_are_append_only_and_decode_payloads(self):
        campaign = marketing_store.create_campaign(
            project_id=self.project["id"],
            task_id=self.task["id"],
            request="Create campaign",
            objective="awareness",
            buyer="ops_manager",
            topic="document handoffs",
            social_platforms=["instagram"],
            video_platform="tiktok",
        )
        marketing_store.add_event(campaign["id"], "founder.variant_selected", {"variant_id": "var_1"})
        events = marketing_store.list_events(campaign["id"])
        self.assertEqual(events[0]["event"], "campaign.queued")
        self.assertEqual(events[-1]["payload"], {"variant_id": "var_1"})


if __name__ == "__main__":
    unittest.main()
