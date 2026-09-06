import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.marketing_store as marketing_store
import core.sales_store as sales_store
import core.state as state
from app.api import app
from fastapi.testclient import TestClient


class OperationsUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "company.db"
        self.old_state_path = state.DB_PATH
        self.old_marketing_path = marketing_store.DB_PATH
        self.old_sales_path = sales_store.DB_PATH
        state.DB_PATH = self.database
        marketing_store.DB_PATH = self.database
        sales_store.DB_PATH = self.database
        state.init_db()
        marketing_store.init_marketing_db()
        sales_store.init_sales_db()
        self.project = state.create_project("Example Company", "company-core")
        state.set_active_project(self.project["id"])
        self.client = TestClient(app)
        self.auth = ("founder", "dashboard-secret")

    def tearDown(self):
        state.DB_PATH = self.old_state_path
        marketing_store.DB_PATH = self.old_marketing_path
        sales_store.DB_PATH = self.old_sales_path
        self.temporary.cleanup()

    def test_research_and_operations_pages_are_split(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            research = self.client.get("/", auth=self.auth)
            operations = self.client.get("/operations", auth=self.auth)
        self.assertEqual(research.status_code, 200)
        self.assertEqual(operations.status_code, 200)
        self.assertIn("Research cockpit", research.text)
        self.assertNotIn("Lead review", research.text)
        self.assertIn("Action launchers", operations.text)
        self.assertIn("New Marketing Campaign", operations.text)
        self.assertIn("New Sales Lead", operations.text)
        self.assertIn("Research Leads", operations.text)
        self.assertIn("Research leads", operations.text)
        self.assertIn("Industry", operations.text)
        self.assertIn("Lusha", operations.text)
        self.assertIn("Apollo", operations.text)
        self.assertIn("Hunter", operations.text)
        self.assertIn("Prospeo", operations.text)

    def test_history_page_renders_archive_layout(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            response = self.client.get("/operations/history", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Action history", response.text)
        self.assertIn("Marketing archive", response.text)
        self.assertIn("Sales archive", response.text)
        self.assertIn("Action stream", response.text)
        self.assertIn('/operations/sales', response.text)
        self.assertIn('/operations/marketing', response.text)

    def test_dedicated_sales_and_marketing_pages_render(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            sales = self.client.get("/operations/sales/discovery", auth=self.auth)
            email = self.client.get("/operations/sales/email", auth=self.auth)
            marketing = self.client.get("/operations/marketing", auth=self.auth)
            marketing_assets = self.client.get("/operations/marketing/assets", auth=self.auth)
            marketing_publishing = self.client.get("/operations/marketing/publishing", auth=self.auth)
        self.assertEqual(sales.status_code, 200)
        self.assertEqual(email.status_code, 200)
        self.assertEqual(marketing.status_code, 200)
        self.assertEqual(marketing_assets.status_code, 200)
        self.assertEqual(marketing_publishing.status_code, 200)
        self.assertIn("Lead discovery control", sales.text)
        self.assertIn("Lead discovery", sales.text)
        self.assertIn("Email outreach", sales.text)
        self.assertIn("Website signups", sales.text)
        self.assertIn("Research Leads", sales.text)
        self.assertIn('data-sales-source-filter="website"', sales.text)
        self.assertNotIn("Email queue", sales.text)
        self.assertIn("Email agent control", email.text)
        self.assertIn("Email queue", email.text)
        self.assertIn("Replies and delivery timeline", email.text)
        self.assertNotIn("Research Leads", email.text)
        self.assertNotIn('data-sales-source-filter="website"', email.text)
        self.assertIn("Marketing campaign control", marketing.text)
        self.assertIn("Campaign Studio", marketing.text)
        self.assertIn("Scene Assets", marketing.text)
        self.assertIn("Publishing", marketing.text)
        self.assertIn("New Marketing Campaign", marketing.text)
        self.assertIn("Voice mode", marketing.text)
        self.assertIn("Campaign direction", marketing.text)
        self.assertNotIn("Pull Scene Images", marketing.text)
        self.assertNotIn("Register Carousel Post", marketing.text)

        self.assertIn("Scene asset control", marketing_assets.text)
        self.assertIn("Pull Scene Images", marketing_assets.text)
        self.assertIn("Downloaded scene media", marketing_assets.text)
        self.assertNotIn("New Marketing Campaign", marketing_assets.text)
        self.assertNotIn("Register Carousel Post", marketing_assets.text)

        self.assertIn("Publishing control", marketing_publishing.text)
        self.assertIn("Register Carousel Post", marketing_publishing.text)
        self.assertIn("Register Video Post", marketing_publishing.text)
        self.assertIn("Destination URL", marketing_publishing.text)
        self.assertIn("CTA label", marketing_publishing.text)
        self.assertIn("forms.example.com", marketing_publishing.text)
        self.assertNotIn("New Marketing Campaign", marketing_publishing.text)
        self.assertNotIn("Pull Scene Images", marketing_publishing.text)
        self.assertIn('/operations/sales', marketing.text)

    def test_sales_filter_uses_real_stage_values_and_failed_send_events(self):
        lead, _ = sales_store.upsert_lead({"email": "ops@example.com", "company": "Example Logistics", "source": "website"})
        sales_store.update_lead(lead["id"], stage="contacted")
        sales_store.add_event(lead["id"], "resend.email.failed", {"reason": "bounce"})

        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            response = self.client.get("/operations", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-sales-filter="new"', response.text)
        self.assertIn('data-sales-filter="enriched"', response.text)
        self.assertIn('data-sales-filter="contacted"', response.text)
        self.assertIn('data-sales-filter="failed"', response.text)
        self.assertIn('data-sales-source-filter="website"', response.text)
        self.assertIn('data-sales-source-filter="popup"', response.text)

    def test_operations_overview_returns_unified_history(self):
        task = state.create_task(project_id=self.project["id"], agent="growth", task_type="campaign", input_text="Create campaign")
        campaign = marketing_store.create_campaign(
            project_id=self.project["id"],
            task_id=task["id"],
            request="Create campaign",
            objective="awareness",
            buyer="ops_manager",
            topic="document handoffs",
            social_platforms=["instagram", "x"],
            video_platform="shorts",
        )
        lead, _ = sales_store.upsert_lead({"email": "ops@example.com", "company": "Example Logistics", "source": "website", "consent": True})
        sales_store.add_event(lead["id"], "lead.reviewed", {"status": "qualified"})
        sales_store.mark_replied(lead["id"], "Re: Walkthrough", "Interested", "<reply-1@example.com>")

        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            with patch("app.company_ops_api.marketing_doctor", return_value={"ok": True, "checks": {}}), patch("app.company_ops_api.sales_doctor", return_value={"ok": True, "checks": {}}):
                response = self.client.get("/company/operations/overview", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["active_project"]["slug"], "company-core")
        self.assertEqual(len(body["marketing_campaigns"]), 1)
        self.assertEqual(len(body["sales_leads"]), 1)
        domains = {item["domain"] for item in body["history"]}
        self.assertIn("marketing", domains)
        self.assertIn("sales", domains)

    def test_direct_campaign_launcher_creates_and_spawns_campaign(self):
        payload = {
            "objective": "awareness",
            "brief": "Freight teams keep working from outdated shipping document versions across handoffs.",
            "social_platforms": ["instagram", "x"],
            "video_platform": "shorts",
            "voice_mode": "tts",
            "voice_transcript": "",
        }
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            with patch("app.marketing_api.spawn") as spawn:
                response = self.client.post("/company/marketing/campaigns", auth=self.auth, json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["campaign"]["topic"], payload["brief"])
        self.assertEqual(body["campaign"]["voice_mode"], "tts")
        self.assertEqual(body["campaign"]["buyer"], "logistics operators")
        spawn.assert_called_once()
        campaigns = marketing_store.list_campaigns(limit=5, project_id=self.project["id"])
        self.assertEqual(len(campaigns), 1)

    def test_manual_carousel_post_creation_and_attribution(self):
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "MARKETING_FORMS_BASE_URL": "https://forms.example.com",
        }, clear=False):
            session = self.client.post(
                "/company/marketing/manual-posts/session",
                auth=self.auth,
                json={"platform": "instagram"},
            )
        self.assertEqual(session.status_code, 200)
        created = session.json()["post"]
        self.assertEqual(created["workflow_status"], "uploading")
        self.assertEqual(created["asset_count"], 0)

        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            uploaded = self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/assets",
                auth=self.auth,
                files={"asset": ("workflow-gaps-slide1.png", b"fakepng", "image/png")},
            )
        self.assertEqual(uploaded.status_code, 200)
        uploaded_post = uploaded.json()["post"]
        self.assertEqual(uploaded_post["asset_count"], 1)
        self.assertEqual(uploaded_post["workflow_status"], "uploading")

        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "MARKETING_FORMS_BASE_URL": "https://forms.example.com",
        }, clear=False):
            finalized = self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/finalize",
                auth=self.auth,
            )
        self.assertEqual(finalized.status_code, 200)
        body = finalized.json()
        self.assertTrue(body["ok"])
        post = body["post"]
        self.assertTrue(post["post_id"].startswith("instagram_carousel_"))
        self.assertEqual(post["campaign_id"], "mkt_instagram_carousel_202609")
        self.assertEqual(post["workflow_status"], "ready")
        self.assertTrue(post["buffer_ready"])
        self.assertIn("utm_source=instagram", post["tracked_url"])
        self.assertIn("forms.example.com", post["tracked_url"])
        self.assertIn("Link in bio", post["caption"])
        self.assertEqual(post["assets"][0]["filename"], "workflow-gaps-slide1.png")

        sales_store.upsert_lead({
            "email": "carousel@example.com",
            "company": "Example Logistics",
            "source": "social_form",
            "post_id": post["post_id"],
            "campaign_id": post["campaign_id"],
            "utm_campaign": post["utm_campaign"],
            "consent": True,
        })
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            listed = self.client.get("/company/marketing/manual-posts", auth=self.auth)
        self.assertEqual(listed.status_code, 200)
        listed_post = listed.json()["posts"][0]
        self.assertEqual(listed_post["attribution"]["leads"], 1)

    def test_manual_carousel_can_push_ready_post_to_buffer(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            session = self.client.post(
                "/company/marketing/manual-posts/session",
                auth=self.auth,
                json={"platform": "instagram"},
            )
        created = session.json()["post"]
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "dashboard-secret"}, clear=False):
            self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/assets",
                auth=self.auth,
                files={"asset": ("workflow-gaps-slide1.png", b"fakepng", "image/png")},
            )
            self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/finalize",
                auth=self.auth,
            )
        fake_output = {"ok": True, "provider": "buffer", "results": [{"platform": "instagram", "status": "draft_confirmed", "post_id": "buf_123"}]}
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "MARKETING_G3_BIN": str(Path(__file__)),
        }, clear=False):
            with patch("app.marketing_api._run", return_value='prefix {"ok": true} suffix'), patch("app.marketing_api._last_json", return_value=fake_output):
                response = self.client.post(
                    f"/company/marketing/manual-posts/{created['id']}/buffer-draft",
                    auth=self.auth,
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["post"]["buffer_status"], "drafted")
        self.assertEqual(body["post"]["workflow_status"], "buffer_draft")
        self.assertEqual(body["result"]["results"][0]["post_id"], "buf_123")

    def test_manual_video_uses_controlled_link_and_creates_buffer_handoff(self):
        project_root = Path(self.temporary.name) / "projects"
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "MARKETING_G3_BIN": str(Path(__file__)),
        }, clear=False), \
             patch("app.marketing_api.PROJECTS_ROOT", project_root):
            session = self.client.post(
                "/company/marketing/manual-posts/session",
                auth=self.auth,
                json={
                    "platform": "x",
                    "post_type": "video",
                    "destination_url": "offers.example.com/ops-checklist",
                    "link_label": "operations checklist",
                },
            )
            self.assertEqual(session.status_code, 200)
            created = session.json()["post"]
            self.assertEqual(created["post_type"], "video")
            self.assertTrue(created["post_id"].startswith("x_video_"))
            self.assertTrue(created["destination_url"].startswith("https://offers.example.com/"))
            self.assertIn("utm_medium=video", created["tracked_url"])

            uploaded = self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/assets",
                auth=self.auth,
                files={"asset": ("visibility-demo.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
            )
            self.assertEqual(uploaded.status_code, 200)

            finalized = self.client.post(
                f"/company/marketing/manual-posts/{created['id']}/finalize",
                auth=self.auth,
            )
            self.assertEqual(finalized.status_code, 200)
            post = finalized.json()["post"]
            self.assertEqual(post["asset_count"], 1)
            self.assertIn("Get the operations checklist", post["caption"])
            self.assertIn(post["tracked_url"], post["caption"])

            fake_output = {
                "ok": True,
                "provider": "buffer",
                "results": [{"platform": "x", "status": "draft_confirmed", "post_id": "buf_video"}],
            }
            with patch("app.marketing_api._run", return_value='{"ok": true}'), \
                 patch("app.marketing_api._last_json", return_value=fake_output):
                pushed = self.client.post(
                    f"/company/marketing/manual-posts/{created['id']}/buffer-draft",
                    auth=self.auth,
                )
            self.assertEqual(pushed.status_code, 200)
            handoff = Path(post["asset_dir"]) / "g3_manual_handoff.json"
            payload = json.loads(handoff.read_text())
            self.assertEqual(payload["drafts"][0]["thread"][0]["media"][0]["path"], "visibility-demo.mp4")
if __name__ == "__main__":
    unittest.main()
