import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.sales_store as sales_store
import core.state as state
from app.api import app
from fastapi.testclient import TestClient


class SalesTunnelSetupTests(unittest.TestCase):
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

    def tearDown(self):
        state.DB_PATH = self.old_state_path
        sales_store.DB_PATH = self.old_sales_path
        self.temporary.cleanup()

    def test_health_is_public_for_tunnel_probes(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_meet_page_is_public_for_booking_cta(self):
        response = self.client.get('/meet')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Schedule a meeting with Company Core', response.text)
        self.assertIn('/static/company-core-logo.svg', response.text)
        self.assertIn('/calendar', response.text)

    def test_calendar_page_is_public_for_cal_booking(self):
        with patch.dict(os.environ, {
            'SALES_CALENDAR_BASE_URL': 'https://calendar.example.com',
            'SALES_CALENDAR_EVENT_PATH': 'book/company',
            'SALES_CALENDAR_BOOKING_URL': '',
            'SALES_CALENDAR_EMBED_URL': '',
        }, clear=False):
            response = self.client.get('/calendar')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Book your operations review', response.text)
        self.assertIn('calendar.example.com', response.text)

    def test_website_intake_accepts_optional_company_website_without_scheme(self):
        payload = {
            'email': 'ops@example.com',
            'full_name': 'Example Operator',
            'company': 'Example Logistics',
            'company_domain': 'example.com',
            'source': 'website',
            'consent': True,
        }
        with patch.dict(os.environ, {'SALES_INTAKE_SECRET': 'lead-secret'}, clear=False):
            response = self.client.post(
                '/integrations/leads/website',
                json=payload,
                headers={'X-Company-Core-Lead-Secret': 'lead-secret'},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        lead = sales_store.get_lead(body['lead_id'])
        self.assertEqual(lead['company_domain'], 'example.com')

    def test_website_intake_accepts_blank_company_website(self):
        payload = {
            'email': 'ops2@example.com',
            'full_name': 'Example Operator',
            'company': 'Example Logistics',
            'company_domain': '',
            'source': 'website',
            'consent': True,
        }
        with patch.dict(os.environ, {'SALES_INTAKE_SECRET': 'lead-secret'}, clear=False):
            response = self.client.post(
                '/integrations/leads/website',
                json=payload,
                headers={'X-Company-Core-Lead-Secret': 'lead-secret'},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        lead = sales_store.get_lead(body['lead_id'])
        self.assertIsNone(lead['company_domain'])

    def test_website_intake_uses_shared_secret_without_dashboard_auth(self):
        payload = {
            'email': 'ops@example.com',
            'full_name': 'Example Operator',
            'company': 'Example Logistics',
            'source': 'website',
            'consent': True,
        }
        with patch.dict(os.environ, {'SALES_INTAKE_SECRET': 'lead-secret'}, clear=False):
            response = self.client.post(
                '/integrations/leads/website',
                json=payload,
                headers={'X-Company-Core-Lead-Secret': 'lead-secret'},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['created'], True)
        self.assertEqual(body['stage'], 'new')

    def test_website_intake_can_auto_contact_new_email_lead(self):
        payload = {
            'email': 'ops@example.com',
            'full_name': 'Example Operator',
            'company': 'Example Logistics',
            'source': 'website',
            'consent': True,
        }

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"id":"re_auto_123"}'

        with patch.dict(os.environ, {
            'SALES_INTAKE_SECRET': 'lead-secret',
            'SALES_AUTO_CONTACT_ENABLED': 'true',
            'SALES_RESEND_API_KEY': 're_test',
            'SALES_RESEND_DOMAIN': 'resend-test.com',
            'SALES_FROM_EMAIL': 'sales@example.com',
            'SALES_REPLY_TO_EMAIL': 'reply@example.com',
        }, clear=False):
            with patch('urllib.request.urlopen', return_value=Response()):
                response = self.client.post(
                    '/integrations/leads/website',
                    json=payload,
                    headers={'X-Company-Core-Lead-Secret': 'lead-secret'},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['auto_contact']['sent'])
        lead = sales_store.get_lead(body['lead_id'])
        self.assertEqual(lead['stage'], 'contacted')


    def test_tally_intake_maps_native_webhook_payload(self):
        payload = {
            'eventId': 'evt_1',
            'eventType': 'FORM_RESPONSE',
            'createdAt': '2026-08-31T08:00:00Z',
            'data': {
                'responseId': 'resp_1',
                'submissionId': 'sub_1',
                'formId': 'form_1',
                'formName': 'Quiz Popup',
                'fields': [
                    {'label': 'Email', 'key': 'email', 'value': 'popup@example.com'},
                    {'label': 'Name', 'key': 'name', 'value': 'Popup Operator'},
                    {'label': 'Company', 'key': 'company', 'value': 'Popup Logistics'},
                    {'label': 'Website', 'key': 'website', 'value': 'popuplogistics.com'},
                    {'label': 'utm_source', 'key': 'hidden_utm_source', 'value': 'instagram'},
                    {'label': 'source', 'key': 'hidden_source', 'value': 'popup_offer'},
                    {'label': 'Consent', 'key': 'consent', 'value': True},
                ],
            },
        }
        with patch.dict(os.environ, {'SALES_INTAKE_SECRET': 'lead-secret'}, clear=False):
            response = self.client.post(
                '/integrations/leads/tally',
                json=payload,
                headers={'X-Company-Core-Lead-Secret': 'lead-secret'},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['provider'], 'tally')
        lead = sales_store.get_lead(body['lead_id'])
        self.assertEqual(lead['email'], 'popup@example.com')
        self.assertEqual(lead['company'], 'Popup Logistics')
        self.assertEqual(lead['company_domain'], 'popuplogistics.com')
        self.assertEqual(lead['source'], 'popup_offer')
        self.assertEqual(lead['utm_source'], 'instagram')

    def test_tally_intake_verifies_signature_when_configured(self):
        payload = {
            'eventType': 'FORM_RESPONSE',
            'data': {
                'formName': 'Quiz Popup',
                'fields': [
                    {'label': 'Email', 'key': 'email', 'value': 'popup@example.com'},
                ],
            },
        }
        raw = json.dumps(payload).encode('utf-8')
        signature = base64.b64encode(
            hmac.new(b'tally-secret', raw, hashlib.sha256).digest()
        ).decode('utf-8')
        with patch.dict(os.environ, {
            'SALES_INTAKE_SECRET': 'lead-secret',
            'TALLY_WEBHOOK_SECRET': 'tally-secret',
        }, clear=False):
            response = self.client.post(
                '/integrations/leads/tally',
                content=raw,
                headers={
                    'Content-Type': 'application/json',
                    'X-Company-Core-Lead-Secret': 'lead-secret',
                    'Tally-Signature': signature,
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_tally_intake_rejects_bad_signature(self):
        payload = {
            'eventType': 'FORM_RESPONSE',
            'data': {
                'fields': [
                    {'label': 'Email', 'key': 'email', 'value': 'popup@example.com'},
                ],
            },
        }
        with patch.dict(os.environ, {
            'SALES_INTAKE_SECRET': 'lead-secret',
            'TALLY_WEBHOOK_SECRET': 'tally-secret',
        }, clear=False):
            response = self.client.post(
                '/integrations/leads/tally',
                json=payload,
                headers={
                    'X-Company-Core-Lead-Secret': 'lead-secret',
                    'Tally-Signature': 'bad-signature',
                },
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'invalid Tally signature')

    def test_sales_reply_ingress_uses_shared_secret_without_dashboard_auth(self):
        payload = {
            'from_email': 'lead@example.com',
            'subject': 'Re: Walkthrough',
            'text': 'Interested',
            'message_id': '<reply-1@example.com>',
        }
        with patch.dict(os.environ, {'SALES_EMAIL_WEBHOOK_SECRET': 'webhook-secret'}, clear=False):
            with patch('app.sales_api.ingest_inbound_email', return_value={'ok': True, 'lead_id': 'lead_1'}):
                response = self.client.post(
                    '/integrations/email/sales',
                    json=payload,
                    headers={'X-Sales-Email-Webhook-Secret': 'webhook-secret'},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'lead_id': 'lead_1'})

    def test_manual_meeting_schedule_updates_lead_stage_and_metadata(self):
        lead, _ = sales_store.upsert_lead({
            'email': 'scheduled@example.com',
            'full_name': 'Scheduled Lead',
            'company': 'Example Logistics',
            'source': 'website',
            'consent': True,
        })
        sales_store.update_lead(lead['id'], stage='contacted')
        with patch.dict(
            os.environ,
            {
                'DASHBOARD_USER': 'test-admin',
                'DASHBOARD_PASSWORD': 'dashboard-secret',
                'SALES_ACTION_TOKEN': 'action-secret',
            },
            clear=False,
        ):
            response = self.client.post(
                f"/company/sales/leads/{lead['id']}/schedule-meeting",
                json={'scheduled_for': '2026-09-05 14:00 CAT', 'note': 'Client requested alignment with ops lead'},
                headers={'X-Founder-Action-Token': 'action-secret'},
                auth=('test-admin', 'dashboard-secret'),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['stage'], 'scheduled')
        updated = sales_store.get_lead(lead['id'])
        self.assertEqual(updated['stage'], 'scheduled')
        self.assertEqual(updated['metadata']['meeting']['scheduled_for'], '2026-09-05 14:00 CAT')
        self.assertEqual(updated['metadata']['meeting']['note'], 'Client requested alignment with ops lead')

    def test_sales_routes_stay_behind_dashboard_auth(self):
        with patch.dict(os.environ, {'DASHBOARD_PASSWORD': 'dashboard-secret'}, clear=False):
            response = self.client.get('/company/sales/doctor')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'Not authenticated')


if __name__ == '__main__':
    unittest.main()
