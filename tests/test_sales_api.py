import base64
import hashlib
import hmac
import json
import os
import sys
import time
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


from fastapi.testclient import TestClient

try:
    from fastapi import HTTPException
except ImportError:
    fastapi_module = types.ModuleType("fastapi")

    class APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda function: function

        post = get

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi_module.APIRouter = APIRouter
    fastapi_module.Depends = lambda *args, **kwargs: None
    fastapi_module.Header = lambda default=None, **_kwargs: default
    fastapi_module.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi_module

from app.api import app
from app.sales_api import doctor, inbound_email, InboundEmailPayload
import core.sales_store as sales_store
import core.state as state


class SalesApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "company.db"
        self.old_state_path = state.DB_PATH
        self.old_sales_path = sales_store.DB_PATH
        state.DB_PATH = self.database
        sales_store.DB_PATH = self.database
        state.init_db()
        sales_store.init_sales_db()
        self.client = TestClient(app)
        self.auth = ("founder", "dashboard-secret")

    def tearDown(self):
        state.DB_PATH = self.old_state_path
        sales_store.DB_PATH = self.old_sales_path
        self.temporary.cleanup()

    def test_doctor_reflects_resend_and_inbound_reply_config(self):
        with patch.dict(os.environ, {
            "HUNTER_API_KEY": "hunter",
            "APOLLO_API_KEY": "apollo",
            "PROSPEO_API_KEY": "prospeo",
            "LUSHA_API_KEY": "lusha",
            "CE_API_KEY": "ce",
            "PDL_API_KEY": "pdl",
            "TALLY_API_KEY": "tally-api",
            "TALLY_WEBHOOK_SECRET": "tally-secret",
            "SALES_INTAKE_SECRET": "lead-secret",
            "SALES_ACTION_TOKEN": "action-secret",
            "SALES_RESEND_API_KEY": "re_test",
            "SALES_RESEND_DOMAIN": "resend-test.com",
            "SALES_FROM_EMAIL": "sales@example.com",
            "SALES_EMAIL_WEBHOOK_SECRET": "webhook-secret",
            "SALES_REPLY_TO_EMAIL": "sales@example.com",
        }, clear=False):
            result = doctor()
        self.assertTrue(result["ok"])
        self.assertTrue(result["checks"]["outbound_email"])
        self.assertTrue(result["checks"]["inbound_replies"])
        self.assertTrue(result["checks"]["tally_api"])
        self.assertTrue(result["checks"]["tally_webhook_secret"])

    def test_inbound_email_requires_shared_secret(self):
        payload = InboundEmailPayload(from_email="ops@example.com", subject="Re: Hello", text="Hi")
        with patch.dict(os.environ, {"SALES_EMAIL_WEBHOOK_SECRET": "expected"}, clear=False):
            with self.assertRaises(HTTPException) as context:
                inbound_email(payload, x_sales_email_webhook_secret="wrong")
        self.assertEqual(context.exception.status_code, 401)

    def test_inbound_email_passes_payload_to_ingest_service(self):
        payload = InboundEmailPayload(from_email="ops@example.com", subject="Re: Hello", text="Hi")
        with patch.dict(os.environ, {"SALES_EMAIL_WEBHOOK_SECRET": "expected"}, clear=False):
            with patch("app.sales_api.ingest_inbound_email", return_value={"ok": True, "lead_id": "lead_1"}) as ingest:
                result = inbound_email(payload, x_sales_email_webhook_secret="expected")
        self.assertEqual(result["lead_id"], "lead_1")
        ingest.assert_called_once()


    def test_resend_webhook_route_verifies_signature_and_dispatches(self):
        payload = json.dumps({"type": "email.delivered", "created_at": "2026-08-31T10:00:00Z", "data": {"email_id": "re_123", "tags": {"lead_id": "lead_1", "draft_id": "draft_1"}}})
        secret = "whsec_dGVzdF9zZWNyZXRfZm9yX3Jlc2VuZA=="
        timestamp = str(int(time.time()))
        message_id = "msg_test_123"
        signed = f"{message_id}.{timestamp}.{payload}".encode("utf-8")
        key = base64.b64decode(secret.split("_", 1)[1])
        signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("utf-8")
        with patch.dict(os.environ, {"SALES_RESEND_WEBHOOK_SECRET": secret}, clear=False):
            with patch("app.sales_api.ingest_resend_event", return_value={"ok": True, "event": "email.delivered", "lead_id": "lead_1"}) as ingest:
                response = self.client.post(
                    "/integrations/resend/sales",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "svix-id": message_id,
                        "svix-timestamp": timestamp,
                        "svix-signature": f"v1,{signature}",
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lead_id"], "lead_1")
        ingest.assert_called_once_with(json.loads(payload))

    def test_prospect_domain_accepts_apollo_provider(self):
        payload = {"domain": "example.com", "limit": 3, "provider": "apollo"}
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.import_domain", return_value=[{"lead": {"id": "lead_1"}, "created": True}]) as import_domain:
                response = self.client.post(
                    "/company/sales/prospect/domain",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                    json=payload,
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["provider"], "apollo")
        import_domain.assert_called_once_with("example.com", 3, "apollo")

    def test_update_draft_route_saves_manual_edits(self):
        lead, _ = sales_store.upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = sales_store.create_draft(lead["id"], "Old subject", "This is the original body long enough for editing.")
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            response = self.client.post(
                f"/company/sales/drafts/{draft['id']}/update",
                auth=self.auth,
                headers={"X-Founder-Action-Token": "action-secret"},
                json={"subject": "New subject", "body": "This is the updated body with enough length to pass validation."},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["subject"], "New subject")

    def test_resolve_company_returns_cached_or_live_profile(self):
        lead, _ = sales_store.upsert_lead({"company": "Example Logistics", "company_domain": "example.com", "source": "prospeo"})
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.resolve_company_profile", return_value={
                "ok": True,
                "cached": False,
                "provider": "pdl",
                "domain": "example.com",
                "lead": {"id": lead["id"]},
                "company_profile": {"domain": "example.com", "summary": {"industry": "Logistics"}},
            }) as resolve_company_mock:
                response = self.client.post(
                    f"/company/sales/leads/{lead['id']}/resolve-company?provider=auto",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["provider"], "pdl")
        resolve_company_mock.assert_called_once_with(lead["id"], provider="auto", force=False)

    def test_draft_preview_returns_context_and_preview(self):
        lead, _ = sales_store.upsert_lead({"email": "ops@example.com", "company": "ODW Logistics", "company_domain": "odwlogistics.com", "source": "prospeo"})
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.preview_draft", return_value={
                "ok": True,
                "provider": "model",
                "lead": {"id": lead["id"]},
                "context": {"company_context": {"industry": "Logistics"}},
                "preview": {"subject": "Operational visibility for ODW", "body": "Short draft", "rationale": "Grounded in company context", "call_to_action": "reply"},
            }) as preview_mock:
                response = self.client.post(
                    f"/company/sales/leads/{lead['id']}/draft-preview",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["preview"]["subject"], "Operational visibility for ODW")
        preview_mock.assert_called_once_with(lead["id"])

    def test_resolve_contact_returns_cached_or_live_contact_data(self):
        lead, _ = sales_store.upsert_lead({"full_name": "Aline Ops", "source": "prospeo", "metadata": {"prospeo_person_id": "person_1"}})
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.resolve_contact", return_value={
                "ok": True,
                "cached": False,
                "provider": "prospeo",
                "status": "resolved",
                "lead": {"id": lead["id"]},
                "contact": {
                    "email": "aline@example.com",
                    "phone": "+250700000000",
                    "whatsapp_candidate": "+250700000000",
                    "ready_for_outreach": True,
                    "needs_generic_fallback": False,
                },
            }) as resolve_contact_mock:
                response = self.client.post(
                    f"/company/sales/leads/{lead['id']}/resolve-contact?provider=prospeo",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "resolved")
        resolve_contact_mock.assert_called_once_with(lead["id"], provider="prospeo", force=False)

    def test_enrich_accepts_provider_query_param(self):
        lead, _ = sales_store.upsert_lead({"email": "ops@example.com", "source": "manual"})
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.enrich_lead", return_value={"id": lead["id"], "stage": "enriched"}) as enrich_lead_mock:
                response = self.client.post(
                    f"/company/sales/leads/{lead['id']}/enrich?provider=apollo",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                )
        self.assertEqual(response.status_code, 200)
        enrich_lead_mock.assert_called_once_with(lead["id"], provider="apollo")

    def test_research_leads_uses_combined_market_endpoint(self):
        payload = {
            "industry": "Logistics and Supply Chain",
            "location": "United States",
            "limit_per_provider": 5,
        }
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.research_market_leads", return_value={
                "results": [{"lead": {"id": "lead_1"}, "created": True}],
                "providers": {"prospeo": 1, "apollo": 1, "lusha": 0},
                "warnings": ["lusha: blocked"],
                "requested": {"industry": "Logistics and Supply Chain", "location": "United States", "limit_per_provider": 5},
            }) as research:
                response = self.client.post(
                    "/company/sales/research/leads",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                    json=payload,
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["provider"], "multi")
        self.assertEqual(body["mode"], "company_first")
        self.assertEqual(body["contact_enrichment"], "on_demand")
        self.assertEqual(body["providers"]["apollo"], 1)
        self.assertEqual(body["warnings"], ["lusha: blocked"])
        research.assert_called_once_with(
            industry="Logistics and Supply Chain",
            location="United States",
            limit_per_provider=5,
        )

    def test_research_suggestions_uses_prospeo_catalog(self):
        payload = {"provider": "prospeo", "kind": "location", "query": "United"}
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.research_prospeo_suggestions", return_value=["United States", "United Kingdom"]) as suggestions:
                response = self.client.post(
                    "/company/sales/research/suggestions",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                    json=payload,
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["provider"], "prospeo")
        self.assertEqual(body["options"], ["United States", "United Kingdom"])
        suggestions.assert_called_once_with(kind="location", query="United")

    def test_research_suggestions_uses_lusha_catalog(self):
        payload = {"provider": "lusha", "kind": "industry", "query": "log"}
        with patch.dict(os.environ, {
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "SALES_ACTION_TOKEN": "action-secret",
        }, clear=False):
            with patch("app.sales_api.research_lusha_suggestions", return_value=["Logistics", "Logistics and Supply Chain"]) as suggestions:
                response = self.client.post(
                    "/company/sales/research/suggestions",
                    auth=self.auth,
                    headers={"X-Founder-Action-Token": "action-secret"},
                    json=payload,
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["provider"], "lusha")
        self.assertEqual(body["options"], ["Logistics", "Logistics and Supply Chain"])
        suggestions.assert_called_once_with(kind="industry", query="log")


if __name__ == "__main__":
    unittest.main()
