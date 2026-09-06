import json
import os
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class SalesContractsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        import core.state
        import core.sales_store
        self.original_state_path = core.state.DB_PATH
        self.original_sales_path = core.sales_store.DB_PATH
        path = Path(self.tempdir.name) / "company.db"
        core.state.DB_PATH = path
        core.sales_store.DB_PATH = path
        core.state.init_db()
        core.sales_store.init_sales_db()

    def tearDown(self):
        import core.state
        import core.sales_store
        core.state.DB_PATH = self.original_state_path
        core.sales_store.DB_PATH = self.original_sales_path
        self.tempdir.cleanup()

    def test_intake_deduplicates_and_scores_inbound(self):
        from core.sales_store import upsert_lead
        first, created = upsert_lead({
            "email": "OPS@Example.com", "full_name": "Aline", "job_title": "Operations Manager",
            "company": "Example Logistics", "country": "Rwanda", "source": "website",
            "message": "We need better freight document control", "consent": True,
        })
        second, created_again = upsert_lead({"email": "ops@example.com", "source": "post_form", "phone": "+250700000000"})
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertGreaterEqual(second["lead_score"], 60)

    def test_send_is_impossible_without_approval(self):
        from core.sales_store import create_draft, upsert_lead
        from services.sales_service import send_approved
        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, this is a sufficiently long body for the contract test.")
        with self.assertRaisesRegex(ValueError, "approval"):
            send_approved(draft["id"])

    def test_unsubscribe_suppression_blocks_drafting(self):
        from core.sales_store import suppress_lead, upsert_lead
        from services.sales_service import build_draft
        import asyncio
        lead, _ = upsert_lead({"email": "ops@example.com", "source": "hunter"})
        suppress_lead(lead["id"], "unsubscribe")
        with self.assertRaisesRegex(ValueError, "suppressed"):
            asyncio.run(build_draft(lead["id"]))

    def test_approved_draft_can_only_be_claimed_once(self):
        from core.sales_store import approve_draft, claim_draft_for_send, create_draft, upsert_lead
        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, this is a sufficiently long body for the contract test.")
        approve_draft(draft["id"])
        claimed = claim_draft_for_send(draft["id"])
        self.assertEqual(claimed["status"], "sending")
        with self.assertRaisesRegex(ValueError, "already sending"):
            claim_draft_for_send(draft["id"])

    def test_update_draft_allows_subject_and_body_edit_before_send(self):
        from core.sales_store import create_draft, update_draft, upsert_lead
        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Old subject", "This is the original body long enough for editing.")
        updated = update_draft(draft["id"], subject="New subject", body="This is the updated body with enough length to pass validation.")
        self.assertEqual(updated["subject"], "New subject")
        self.assertIn("updated body", updated["body"])

    def test_reply_draft_preserves_thread_reference(self):
        from core.sales_store import create_draft, get_draft, mark_replied, upsert_lead
        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        mark_replied(lead["id"], "Re: Cargo records", "Please show me the workflow.", "<reply-1@example.com>")
        draft = create_draft(
            lead["id"], "Re: Cargo records", "Thank you. Here is the next step for the walkthrough.",
            kind="reply", in_reply_to="<reply-1@example.com>",
        )
        stored = get_draft(draft["id"])
        self.assertEqual(stored["kind"], "reply")
        self.assertEqual(stored["in_reply_to"], "<reply-1@example.com>")

    def test_hunter_key_uses_header_not_query(self):
        from services.hunter import HunterClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"data":{"emails":[]}}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.headers.get("X-api-key")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            HunterClient("secret").domain_search("example.com")
        self.assertNotIn("secret", captured["url"])
        self.assertEqual(captured["key"], "secret")

    def test_apollo_key_uses_header_not_query(self):
        from services.apollo import ApolloClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"people":[]}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.headers.get("X-api-key")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            ApolloClient("secret").people_search("example.com", 3)
        self.assertNotIn("secret", captured["url"])
        self.assertEqual(captured["key"], "secret")

    def test_company_enrich_key_uses_authorization_header(self):
        from services.company_enrich import CompanyEnrichClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"name":"Example Logistics","domain":"example.com"}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            CompanyEnrichClient("secret").enrich_company("example.com")
        self.assertIn("domain=example.com", captured["url"])
        self.assertEqual(captured["auth"], "Bearer secret")

    def test_company_profile_cleanup_builds_usage_context(self):
        from services.company_enrich import CompanyEnrichClient, company_usage_profile

        payload = {
            "company": {
                "name": "ODW Logistics",
                "domain": "https://www.odwlogistics.com",
                "description": "Integrated logistics provider for warehousing, transportation, retail consolidation, and supply chain visibility.",
                "industry": "Logistics",
                "employeeRange": "201-500",
                "location": {"city": "Columbus", "state": "OH", "country": "United States"},
                "keywords": ["warehousing", "transportation", "retail compliance", "supply chain visibility"],
                "services": ["Managed Transportation", "Retail Consolidation", "Distribution & Fulfillment"],
                "social_media": {"linkedin": "https://www.linkedin.com/company/odw-logistics"},
            }
        }

        summary = CompanyEnrichClient("secret").extract_company_profile(payload)
        usage = company_usage_profile(summary)

        self.assertEqual(summary["domain"], "odwlogistics.com")
        self.assertEqual(summary["location"]["country"], "United States")
        self.assertIn("Managed Transportation", summary["specialties"])
        self.assertEqual(usage["industry"], "Logistics")
        self.assertIn("supply chain visibility", " ".join(usage["personalization_points"]).lower())
        self.assertTrue(any("document" in item or "visibility" in item for item in usage["suggested_pain_points"]))

    def test_pdl_key_uses_header_not_query(self):
        from services.pdl import PeopleDataLabsClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"name":"Example Logistics","website":"example.com"}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.headers.get("X-api-key") or request.headers.get("X-Api-key") or request.headers.get("X-Api-Key")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            PeopleDataLabsClient("secret").enrich_company("example.com")
        self.assertIn("website=example.com", captured["url"])
        self.assertEqual(captured["key"], "secret")

    def test_company_profile_auto_rotation_falls_back_to_pdl(self):
        from core.sales_store import get_lead, upsert_lead
        from services.company_enrich import CompanyEnrichError
        from services.sales_service import resolve_company_profile

        lead, _ = upsert_lead({"company": "Example Logistics", "company_domain": "https://example.com", "source": "prospeo"})

        with patch.dict(os.environ, {"CE_API_KEY": "ce", "PDL_API_KEY": "pdl"}, clear=False):
            with patch("services.sales_service.CompanyEnrichClient") as ce_mock, patch("services.sales_service.PeopleDataLabsClient") as pdl_mock:
                ce_mock.return_value.enrich_company.side_effect = CompanyEnrichError("ce down", 502)
                pdl_mock.return_value.enrich_company.return_value = {
                    "display_name": "Example Logistics",
                    "website": "example.com",
                    "industry": "logistics",
                    "location": {"country": "united states"},
                }
                pdl_mock.return_value.extract_company_profile.return_value = {
                    "name": "Example Logistics",
                    "domain": "example.com",
                    "website": "https://example.com",
                    "description": "Logistics company",
                    "industry": "logistics",
                    "industries": ["logistics"],
                    "categories": [],
                    "keywords": [],
                    "employee_count": None,
                    "employee_range": None,
                    "revenue_range": None,
                    "linkedin_url": None,
                    "location": {"country": "united states"},
                    "technologies": [],
                    "specialties": [],
                    "target_markets": [],
                    "signals": {},
                }
                result = resolve_company_profile(lead["id"], provider="auto", force=True)

        stored = get_lead(lead["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "pdl")
        self.assertEqual(stored["company_profile"]["provider"], "pdl")
        self.assertEqual(result["attempts"][0]["provider"], "companyenrich")

    def test_company_profile_resolution_uses_domain_and_caches_result(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import resolve_company_profile

        lead, _ = upsert_lead({"company": "Example Logistics", "company_domain": "https://example.com", "source": "prospeo"})

        with patch("services.sales_service.CompanyEnrichClient") as CompanyEnrichClientMock:
            client = CompanyEnrichClientMock.return_value
            client.enrich_company.return_value = {
                "name": "Example Logistics",
                "domain": "example.com",
                "description": "Freight operations platform",
                "industry": "Logistics",
                "employeeRange": "51-100",
                "location": {"country": "United States"},
                "keywords": ["freight", "3pl"],
            }
            client.extract_company_profile.return_value = {
                "name": "Example Logistics",
                "domain": "example.com",
                "website": "example.com",
                "description": "Freight operations platform",
                "industry": "Logistics",
                "industries": [],
                "categories": [],
                "keywords": ["freight", "3pl"],
                "employee_count": None,
                "employee_range": "51-100",
                "revenue_range": None,
                "linkedin_url": None,
                "location": {"country": "United States"},
                "technologies": [],
            }
            first = resolve_company_profile(lead["id"])
            second = resolve_company_profile(lead["id"])

        stored = get_lead(lead["id"])
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(stored["company_profile"]["domain"], "example.com")
        self.assertEqual(stored["company_profile"]["summary"]["industry"], "Logistics")

    def test_apollo_enrichment_updates_lead(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import enrich_lead

        lead, _ = upsert_lead({
            "full_name": "Aline Ops",
            "company_domain": "example.com",
            "source": "apollo",
        })

        with patch("services.sales_service.ApolloClient") as ApolloClientMock:
            ApolloClientMock.return_value.match_person.return_value = {
                "name": "Aline Ops",
                "email": "aline@example.com",
                "title": "Operations Lead",
                "country": "Rwanda",
                "email_status": "verified",
                "organization": {"name": "Example Logistics", "primary_domain": "example.com"},
            }
            updated = enrich_lead(lead["id"], provider="apollo")

        stored = get_lead(lead["id"])
        self.assertEqual(updated["email"], "aline@example.com")
        self.assertEqual(stored["company"], "Example Logistics")
        self.assertEqual(stored["stage"], "enriched")
        self.assertGreaterEqual(stored["lead_score"], 1)

    def test_prospeo_key_uses_header_not_body_or_query(self):
        from services.prospeo import ProspeoClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"results": []}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.headers.get("X-key")
            captured["body"] = request.data.decode("utf-8")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            ProspeoClient("secret").search_person({"person_job_title": {"include": ["Operations Manager"], "match_mode": "CONTAINS"}})
        self.assertNotIn("secret", captured["url"])
        self.assertNotIn("secret", captured["body"])
        self.assertEqual(captured["key"], "secret")

    def test_contact_resolution_uses_prospeo_person_id_and_caches_result(self):
        from services.sales_service import resolve_contact
        from core.sales_store import get_lead, upsert_lead

        lead, _ = upsert_lead({
            "full_name": "Aline Ops",
            "source": "prospeo",
            "metadata": {"prospeo_person_id": "person_1"},
        })

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            client = ProspeoClientMock.return_value
            client.enrich_person.return_value = {
                "person_id": "person_1",
                "email": {"email": "aline@example.com", "status": "VERIFIED"},
                "mobile": {"number": "+250700000000"},
                "linkedin_url": "https://linkedin.com/in/aline",
            }
            client.extract_contact_fields.return_value = {
                "email": "aline@example.com",
                "phone": "+250700000000",
                "verification_status": "VERIFIED",
                "linkedin_url": "https://linkedin.com/in/aline",
                "person_id": "person_1",
            }
            first = resolve_contact(lead["id"])
            second = resolve_contact(lead["id"])

        stored = get_lead(lead["id"])
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(stored["email"], "aline@example.com")
        self.assertEqual(stored["phone"], "+250700000000")
        self.assertEqual(stored["metadata"]["contact_resolution"]["status"], "resolved")
        self.assertTrue(stored["contact_profile"]["ready_for_outreach"])
        self.assertFalse(stored["contact_profile"]["needs_generic_fallback"])

    def test_contact_profile_marks_generic_fallback_when_no_contact_found(self):
        from core.sales_store import get_lead, record_contact_resolution, upsert_lead

        lead, _ = upsert_lead({"full_name": "Aline Ops", "source": "prospeo"})
        record_contact_resolution(lead["id"], provider="prospeo", status="missing", details={})
        stored = get_lead(lead["id"])

        self.assertEqual(stored["contact_profile"]["status"], "missing")
        self.assertFalse(stored["contact_profile"]["ready_for_outreach"])
        self.assertTrue(stored["contact_profile"]["needs_generic_fallback"])

    def test_prospeo_research_imports_search_results(self):
        from services.sales_service import research_prospeo_leads

        payload = {
            "results": [
                {
                    "person": {
                        "person_id": "person_1",
                        "full_name": "Aline Ops",
                        "current_job_title": "Operations Manager",
                        "linkedin_url": "https://linkedin.com/in/aline",
                        "location": {"country": "Rwanda"},
                    },
                    "company": {"name": "Example Logistics", "website": "example.com", "company_id": "cmp_1"},
                }
            ]
        }

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            ProspeoClientMock.return_value.search_person.return_value = payload
            results = research_prospeo_leads(
                job_title="Operations Manager",
                service_keywords=["freight forwarding"],
                location="Rwanda",
                company_size="51-100",
                limit=5,
            )

        self.assertEqual(len(results), 1)
        lead = results[0]["lead"]
        self.assertEqual(lead["source"], "prospeo")
        self.assertEqual(lead["company"], "Example Logistics")

    def test_prospeo_research_retries_with_broader_filters_when_first_pass_is_empty(self):
        from services.sales_service import research_prospeo_leads

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            client = ProspeoClientMock.return_value
            client.search_person.side_effect = [
                {"results": []},
                {
                    "results": [
                        {
                            "person": {
                                "person_id": "person_2",
                                "full_name": "Aline Ops",
                                "current_job_title": "Operations Manager",
                            },
                            "company": {"name": "Example Logistics", "website": "example.com", "company_id": "cmp_1"},
                        }
                    ]
                },
            ]
            results = research_prospeo_leads(
                job_title="Operations Manager",
                service_keywords=["freight forwarding"],
                location="Rwanda",
                company_size="51-100",
                limit=5,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(client.search_person.call_count, 2)
        first_filters = client.search_person.call_args_list[0].args[0]
        second_filters = client.search_person.call_args_list[1].args[0]
        self.assertIn("company_location_search", first_filters)
        self.assertNotIn("company_location_search", second_filters)

    def test_prospeo_market_research_imports_nested_results(self):
        from services.sales_service import research_prospeo_market_leads

        payload = {
            "results": [
                {
                    "person": {
                        "person_id": "person_9",
                        "full_name": "Aline Ops",
                        "current_job_title": "Operations Manager",
                        "location": {"country": "Rwanda"},
                    },
                    "company": {"name": "Example Logistics", "website": "example.com", "company_id": "cmp_1"},
                }
            ]
        }

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            ProspeoClientMock.return_value.search_person.return_value = payload
            results = research_prospeo_market_leads(industry="Logistics", location="Rwanda", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["lead"]["company"], "Example Logistics")
        self.assertIsNone(results[0]["lead"]["email"])
        self.assertTrue(results[0]["lead"]["metadata"]["company_candidate"])

    def test_prospeo_market_research_keeps_one_candidate_per_domain(self):
        from services.sales_service import research_prospeo_market_leads

        payload = {
            "results": [
                {
                    "person": {"person_id": "person_1", "full_name": "First Person"},
                    "company": {"name": "Example Logistics", "website": "https://example.com"},
                },
                {
                    "person": {"person_id": "person_2", "full_name": "Second Person"},
                    "company": {"name": "Example Logistics", "website": "www.example.com/about"},
                },
                {
                    "person": {"person_id": "person_3", "full_name": "Other Person"},
                    "company": {"name": "Other Freight", "website": "other.example"},
                },
            ]
        }

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            ProspeoClientMock.return_value.search_person.return_value = payload
            results = research_prospeo_market_leads(industry="Logistics", location="Rwanda", limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual({item["lead"]["company_domain"] for item in results}, {"example.com", "other.example"})
        ProspeoClientMock.return_value.search_person.assert_called_once()

    def test_prospeo_enrichment_updates_lead(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import enrich_lead

        lead, _ = upsert_lead({
            "full_name": "Aline Ops",
            "source": "prospeo",
            "metadata": {"prospeo_person_id": "person_1"},
        })

        with patch("services.sales_service.ProspeoClient") as ProspeoClientMock:
            ProspeoClientMock.return_value.enrich_person.return_value = {
                "person_id": "person_1",
                "full_name": "Aline Ops",
                "current_job_title": "Operations Manager",
                "location": {"country": "Rwanda"},
                "email": {"email": "aline@example.com", "status": "valid"},
                "mobile": {"number": "+250700000000"},
                "company": {"name": "Example Logistics", "website": "example.com"},
            }
            updated = enrich_lead(lead["id"], provider="prospeo")

        stored = get_lead(lead["id"])
        self.assertEqual(updated["email"], "aline@example.com")
        self.assertEqual(stored["company_domain"], "example.com")
        self.assertEqual(stored["stage"], "qualified")
        self.assertGreaterEqual(stored["lead_score"], 60)

    def test_prospeo_enrich_person_nests_person_id_under_data(self):
        from services.prospeo import ProspeoClient

        with patch.object(ProspeoClient, "_request", return_value={"person": {"person_id": "person_1"}}) as request:
            result = ProspeoClient("secret").enrich_person("person_1")

        self.assertEqual(result["person_id"], "person_1")
        request.assert_called_once_with(
            "enrich-person",
            {
                "only_verified_email": True,
                "data": {"person_id": "person_1"},
            },
        )

    def test_lusha_key_uses_header_not_query(self):
        from services.lusha import LushaClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"values": []}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.headers.get("Api_key") or request.headers.get("Api-Key") or request.headers.get("api_key")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            LushaClient("secret").company_filter_values("sizes")
        self.assertNotIn("secret", captured["url"])
        self.assertEqual(captured["key"], "secret")

    def test_lusha_industry_suggestions_do_not_send_query_param(self):
        from services.lusha import LushaClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"values": ["Logistics", "Software"]}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            LushaClient("secret").company_filter_values("industriesLabels", "LOG")

        self.assertNotIn("query=", captured["url"])

    def test_lusha_job_title_suggestions_return_empty_list(self):
        from services.sales_service import research_lusha_suggestions

        self.assertEqual(research_lusha_suggestions(kind="job_title", query="oper"), [])

    def test_lusha_research_uses_minimum_upstream_page_size(self):
        from services.lusha import LushaClient
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"results": []}'

        def fake_open(request, timeout):
            captured["body"] = request.data.decode("utf-8")
            return Response()

        with patch("urllib.request.urlopen", fake_open):
            LushaClient("secret").prospect_contacts(job_title="Operations Manager", location="Rwanda", limit=5)

        body = json.loads(captured["body"])
        self.assertEqual(body["pagination"]["size"], 10)
        self.assertEqual(body["filters"]["contacts"]["include"]["locations"], [{"country": "Rwanda"}])
        self.assertEqual(body["options"]["maxContactsPerCompany"], 1)

    def test_domain_contact_inherits_saved_company_discovery_context(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import _draft_lead_context, import_domain

        candidate, _ = upsert_lead({
            "full_name": "Example Logistics",
            "company": "Example Logistics",
            "company_domain": "example.com",
            "country": "Rwanda",
            "source": "prospeo",
            "source_detail": "company_market_research",
            "metadata": {
                "company_candidate": True,
                "research_filters": {"industry": "Logistics", "location": "Rwanda"},
            },
        })
        hunter_result = [{
            "value": "aline@example.com",
            "first_name": "Aline",
            "last_name": "Ops",
            "position": "Operations Manager",
            "company": None,
            "verification": {"status": "valid"},
        }]

        with patch("services.sales_service.HunterClient") as HunterClientMock:
            HunterClientMock.return_value.domain_search.return_value = hunter_result
            results = import_domain("example.com", limit=1, provider="hunter")

        contact = results[0]["lead"]
        self.assertEqual(contact["company"], "Example Logistics")
        self.assertEqual(contact["country"], "Rwanda")
        self.assertEqual(contact["metadata"]["company_discovery"]["research_filters"]["industry"], "Logistics")
        self.assertEqual(_draft_lead_context(contact)["company_discovery"]["research_filters"]["location"], "Rwanda")
        self.assertEqual(results[0]["company_candidate_id"], candidate["id"])
        self.assertIn(contact["id"], get_lead(candidate["id"])["metadata"]["contact_lead_ids"])

    def test_existing_domain_contact_gets_discovery_context_when_drafting(self):
        from core.sales_store import upsert_lead
        from services.sales_service import _draft_lead_context

        upsert_lead({
            "company": "Example Logistics",
            "company_domain": "example.com",
            "source": "lusha",
            "source_detail": "company_market_research",
            "metadata": {
                "company_candidate": True,
                "research_filters": {"industry": "Logistics", "location": "Rwanda"},
            },
        })
        contact, _ = upsert_lead({
            "email": "aline@example.com",
            "company_domain": "example.com",
            "source": "hunter",
        })

        context = _draft_lead_context(contact)

        self.assertEqual(context["company_discovery"]["source"], "lusha")
        self.assertEqual(context["company_discovery"]["research_filters"]["industry"], "Logistics")

    def test_apollo_market_research_imports_results(self):
        from services.sales_service import research_apollo_market_leads

        people_payload = [
            {
                "id": "apollo_1",
                "first_name": "Aline",
                "last_name": "Ops",
                "title": "Operations Manager",
                "organization": {"name": "Example Logistics", "primary_domain": "example.com"},
            }
        ]

        with patch("services.sales_service.ApolloClient") as ApolloClientMock:
            client = ApolloClientMock.return_value
            client.people_market_search.return_value = people_payload
            results = research_apollo_market_leads(industry="Logistics", location="Rwanda", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["lead"]["source"], "apollo")
        self.assertIsNone(results[0]["lead"]["email"])
        self.assertTrue(results[0]["lead"]["metadata"]["company_candidate"])
        client.bulk_match.assert_not_called()

    def test_lusha_research_imports_search_results(self):
        from services.sales_service import research_lusha_leads

        search_payload = {
            "requestId": "req_1",
            "results": [
                {
                    "id": "contact_1",
                    "firstName": "Aline",
                    "lastName": "Ops",
                    "jobTitle": {"title": "Operations Manager"},
                    "company": {"name": "Example Logistics", "domain": "example.com"},
                }
            ],
        }
        enrich_payload = {
            "contacts": [
                {
                    "id": "contact_1",
                    "firstName": "Aline",
                    "lastName": "Ops",
                    "jobTitle": {"title": "Operations Manager"},
                    "company": {"name": "Example Logistics", "domain": "example.com", "location": {"country": "Rwanda"}},
                    "emails": [{"value": "aline@example.com"}],
                    "phones": [{"value": "+250700000000"}],
                }
            ]
        }

        with patch("services.sales_service.LushaClient") as LushaClientMock:
            client = LushaClientMock.return_value
            client.prospect_contacts.return_value = search_payload
            client.enrich_contacts.return_value = enrich_payload
            results = research_lusha_leads(
                job_title="Operations Manager",
                service_keywords=["freight forwarding"],
                location="Rwanda",
                company_size="51-100",
                limit=5,
            )

        self.assertEqual(len(results), 1)
        lead = results[0]["lead"]
        self.assertEqual(lead["source"], "lusha")
        self.assertEqual(lead["company"], "Example Logistics")

    def test_lusha_market_research_does_not_enrich_contacts(self):
        from services.sales_service import research_lusha_market_leads

        search_payload = {
            "requestId": "req_1",
            "results": [
                {
                    "id": "contact_1",
                    "company": {"id": "company_1", "name": "Example Logistics", "domain": "example.com"},
                },
                {
                    "id": "contact_2",
                    "company": {"id": "company_1", "name": "Example Logistics", "domain": "example.com"},
                },
            ],
        }

        with patch("services.sales_service.LushaClient") as LushaClientMock:
            client = LushaClientMock.return_value
            client.prospect_contacts.return_value = search_payload
            results = research_lusha_market_leads(industry="Logistics", location="Rwanda", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["lead"]["company_domain"], "example.com")
        client.enrich_contacts.assert_not_called()

    def test_lusha_403_is_reframed_as_provider_block(self):
        from services.lusha import LushaClient, LushaError
        import urllib.error
        import urllib.request

        request = urllib.request.Request("https://api.lusha.com/v3/contacts/prospecting")
        response_body = b'{"message":"The site owner has blocked access based on your browser\'s signature."}'

        class Http403(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

            def read(self):
                return response_body

        with patch("urllib.request.urlopen", side_effect=Http403()):
            with self.assertRaises(LushaError) as ctx:
                LushaClient("secret").prospect_contacts(job_title="Operations Manager", location="Rwanda", limit=5)

        self.assertIn("provider-side access block", str(ctx.exception))

    def test_combined_market_research_returns_provider_breakdown_and_warnings(self):
        from services.lusha import LushaError
        from services.sales_service import research_market_leads

        with patch("services.sales_service.research_prospeo_market_leads", return_value=[{"lead": {"id": "lead_1"}, "created": True}]),              patch("services.sales_service.research_apollo_market_leads", return_value=[{"lead": {"id": "lead_2"}, "created": True}]),              patch("services.sales_service.research_lusha_market_leads", side_effect=LushaError("blocked", 403)):
            result = research_market_leads(industry="Logistics", location="Rwanda", limit_per_provider=5)

        self.assertEqual(result["providers"]["prospeo"], 1)
        self.assertEqual(result["providers"]["apollo"], 1)
        self.assertEqual(result["providers"]["lusha"], 0)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["mode"], "company_first")
        self.assertEqual(result["contact_enrichment"], "on_demand")

    def test_combined_market_research_deduplicates_provider_domains(self):
        from services.sales_service import research_market_leads

        prospeo = {"lead": {"id": "lead_1", "company": "Example", "company_domain": "example.com", "source": "prospeo"}, "created": True}
        lusha = {"lead": {"id": "lead_2", "company": "Example Inc", "company_domain": "https://www.example.com", "source": "lusha"}, "created": True}
        with patch("services.sales_service.research_prospeo_market_leads", return_value=[prospeo]), \
             patch("services.sales_service.research_apollo_market_leads", return_value=[]), \
             patch("services.sales_service.research_lusha_market_leads", return_value=[lusha]):
            result = research_market_leads(industry="Logistics", location="Rwanda", limit_per_provider=5)

        self.assertEqual(len(result["results"]), 1)

    def test_lusha_enrichment_updates_lead(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import enrich_lead

        lead, _ = upsert_lead({
            "full_name": "Aline Ops",
            "source": "lusha",
            "metadata": {"lusha_contact_id": "contact_1", "lusha_request_id": "req_1"},
        })

        with patch("services.sales_service.LushaClient") as LushaClientMock:
            LushaClientMock.return_value.enrich_contacts.return_value = {
                "contacts": [
                    {
                        "id": "contact_1",
                        "firstName": "Aline",
                        "lastName": "Ops",
                        "jobTitle": {"title": "Operations Manager"},
                        "company": {"name": "Example Logistics", "domain": "example.com", "location": {"country": "Rwanda"}},
                        "emails": [{"value": "aline@example.com"}],
                        "phones": [{"value": "+250700000000"}],
                    }
                ]
            }
            updated = enrich_lead(lead["id"], provider="lusha")

        stored = get_lead(lead["id"])
        self.assertEqual(updated["email"], "aline@example.com")
        self.assertEqual(stored["company_domain"], "example.com")
        self.assertEqual(stored["stage"], "enriched")
        self.assertGreaterEqual(stored["lead_score"], 1)

    def test_preview_draft_falls_back_when_model_is_unavailable(self):
        from core.sales_store import upsert_company_profile, upsert_lead
        from services.sales_service import preview_draft

        lead, _ = upsert_lead({
            "email": "ops@example.com",
            "full_name": "Aline Ops",
            "job_title": "Operations Manager",
            "company": "ODW Logistics",
            "company_domain": "odwlogistics.com",
            "source": "prospeo",
        })
        upsert_company_profile(
            "odwlogistics.com",
            provider="companyenrich",
            status="resolved",
            company_name="ODW Logistics",
            summary={
                "name": "ODW Logistics",
                "domain": "odwlogistics.com",
                "industry": "Logistics",
                "specialties": ["Managed Transportation"],
                "signals": {
                    "summary_line": "ODW Logistics: integrated logistics and transportation services.",
                    "suggested_pain_points": ["document and milestone visibility across operations"],
                },
            },
            raw={"company": {"name": "ODW Logistics"}},
        )

        with patch("agents.sales.draft_outreach", AsyncMock(side_effect=RuntimeError("model offline"))):
            result = __import__("asyncio").run(preview_draft(lead["id"]))

        self.assertEqual(result["provider"], "fallback")
        self.assertIn("Operational visibility", result["preview"]["subject"])
        self.assertIn("document and milestone visibility", result["preview"]["body"])

    def test_preview_draft_returns_clean_context_and_generated_copy(self):
        from core.sales_store import upsert_company_profile, upsert_lead
        from services.sales_service import preview_draft

        lead, _ = upsert_lead({
            "email": "ops@example.com",
            "full_name": "Aline Ops",
            "job_title": "Operations Manager",
            "company": "ODW Logistics",
            "company_domain": "odwlogistics.com",
            "source": "prospeo",
        })
        upsert_company_profile(
            "odwlogistics.com",
            provider="companyenrich",
            status="resolved",
            company_name="ODW Logistics",
            summary={
                "name": "ODW Logistics",
                "domain": "odwlogistics.com",
                "website": "https://www.odwlogistics.com",
                "description": "Integrated logistics provider for warehousing and transportation.",
                "industry": "Logistics",
                "keywords": ["warehousing", "transportation", "supply chain visibility"],
                "specialties": ["Managed Transportation", "Retail Consolidation"],
                "employee_range": "201-500",
                "location": {"city": "Columbus", "country": "United States"},
                "signals": {
                    "summary_line": "ODW Logistics: Integrated logistics provider for warehousing and transportation.",
                    "operational_focus": "Managed Transportation, Retail Consolidation",
                    "personalization_points": [
                        "Core services: Managed Transportation, Retail Consolidation",
                    ],
                    "suggested_pain_points": [
                        "document and milestone visibility across operations",
                    ],
                },
            },
            raw={"company": {"name": "ODW Logistics"}},
        )

        class Draft:
            subject = "Operational visibility for ODW"
            body = "Example email body grounded in the company profile."
            rationale = "Uses saved company context and direct contact readiness."
            call_to_action = "reply"

        async def fake_draft(_payload):
            return Draft()

        with patch("agents.sales.draft_outreach", AsyncMock(side_effect=fake_draft)):
            result = __import__("asyncio").run(preview_draft(lead["id"]))

        self.assertTrue(result["ok"])
        self.assertEqual(result["context"]["company_context"]["industry"], "Logistics")
        self.assertEqual(result["preview"]["subject"], "Operational visibility for ODW")
        self.assertIn("document and milestone visibility", result["context"]["company_context"]["suggested_pain_points"][0])

    def test_build_draft_passes_clean_company_context_to_agent(self):
        from core.sales_store import upsert_company_profile, upsert_lead
        from services.sales_service import build_draft

        lead, _ = upsert_lead({
            "email": "ops@example.com",
            "full_name": "Aline Ops",
            "job_title": "Operations Manager",
            "company": "ODW Logistics",
            "company_domain": "odwlogistics.com",
            "source": "prospeo",
        })
        upsert_company_profile(
            "odwlogistics.com",
            provider="companyenrich",
            status="resolved",
            company_name="ODW Logistics",
            summary={
                "name": "ODW Logistics",
                "domain": "odwlogistics.com",
                "website": "https://www.odwlogistics.com",
                "description": "Integrated logistics provider for warehousing and transportation.",
                "industry": "Logistics",
                "keywords": ["warehousing", "transportation", "supply chain visibility"],
                "specialties": ["Managed Transportation", "Retail Consolidation"],
                "employee_range": "201-500",
                "location": {"city": "Columbus", "country": "United States"},
                "signals": {
                    "summary_line": "ODW Logistics: Integrated logistics provider for warehousing and transportation.",
                    "operational_focus": "Managed Transportation, Retail Consolidation",
                    "personalization_points": [
                        "Core services: Managed Transportation, Retail Consolidation",
                        "Visible themes: warehousing, transportation, supply chain visibility",
                    ],
                    "suggested_pain_points": [
                        "document and milestone visibility across operations",
                    ],
                },
            },
            raw={"company": {"name": "ODW Logistics"}},
        )

        captured = {}

        class Draft:
            subject = "Operational control for ODW"
            body = "Short draft body with a grounded logistics observation."

        async def fake_draft(payload):
            captured["payload"] = payload
            return Draft()

        async_mock = AsyncMock(side_effect=fake_draft)
        with patch("agents.sales.draft_outreach", async_mock):
            draft = __import__("asyncio").run(build_draft(lead["id"]))

        self.assertEqual(draft["subject"], "Operational control for ODW")
        self.assertEqual(captured["payload"]["company_context"]["industry"], "Logistics")
        self.assertIn("Managed Transportation", captured["payload"]["company_context"]["specialties"])
        self.assertTrue(captured["payload"]["contact_profile"]["ready_for_outreach"])

    def test_resend_send_marks_draft_sent(self):
        from core.sales_store import approve_draft, create_draft, get_draft, get_lead, upsert_lead
        from services.sales_service import send_approved

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, body for resend transport test.")
        approve_draft(draft["id"])

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"id":"re_123"}'

        def fake_open(request, timeout):
            self.assertEqual(request.full_url, "https://api.resend.com/emails")
            self.assertEqual(request.headers.get("Authorization"), "Bearer re_test")
            payload = request.data.decode("utf-8")
            self.assertIn('"from": "Example Company <sales@resend-test.com>"', payload)
            self.assertIn('"reply_to": "reply@example.com"', payload)
            self.assertIn('"html":', payload)
            self.assertIn("Schedule a meeting", payload)
            self.assertIn("Built for your team operational workflows.", payload)
            return Response()

        with patch.dict(os.environ, {
            "COMPANY_NAME": "Example Company",
            "SALES_PUBLIC_BASE_URL": "https://sales.example.com",
            "SALES_SCHEDULE_URL": "https://sales.example.com/meet",
            "SALES_RESEND_API_KEY": "re_test",
            "SALES_RESEND_DOMAIN": "resend-test.com",
            "SALES_FROM_EMAIL": "sales@example.com",
            "SALES_REPLY_TO_EMAIL": "reply@example.com",
        }, clear=False):
            with patch("urllib.request.urlopen", fake_open):
                result = send_approved(draft["id"])

        stored = get_draft(draft["id"])
        updated_lead = get_lead(lead["id"])
        self.assertEqual(result["message_id"], "re_123")
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(stored["provider_message_id"], "re_123")
        self.assertEqual(updated_lead["stage"], "contacted")
        self.assertEqual(updated_lead["interactions"][0]["metadata"]["provider"], "resend")
        self.assertEqual(updated_lead["interactions"][0]["metadata"]["from_email"], "Example Company <sales@resend-test.com>")


    def test_resend_payload_includes_branded_html_and_text(self):
        from services.sales_service import _resend_payload_for_draft

        draft = {
            "id": "draft_1",
            "subject": "Reducing demurrage at ACME",
            "body": "Hi ACME,\n\nWe help reduce delays across freight workflows.",
        }
        lead = {
            "id": "lead_1",
            "email": "ops@example.com",
            "company": "ACME",
        }

        with patch.dict(os.environ, {
            "COMPANY_NAME": "Example Company",
            "SALES_PUBLIC_BASE_URL": "https://sales.example.com",
            "SALES_SCHEDULE_URL": "https://sales.example.com/meet",
        }, clear=False):
            payload = _resend_payload_for_draft(
                draft,
                lead,
                sender="Example Company <sales@resend-test.com>",
                reply_to="reply@example.com",
            )

        self.assertEqual(payload["text"], draft["body"] + "\n\nSchedule a meeting: https://sales.example.com/meet")
        self.assertIn("Reducing demurrage at ACME", payload["html"])
        self.assertIn("Built for ACME operational workflows.", payload["html"])
        self.assertIn("Schedule a meeting", payload["html"])
        self.assertIn("https://sales.example.com/meet", payload["html"] )
        self.assertIn("company-core-logo.svg", payload["html"])


    def test_resend_delivery_event_records_status_against_sent_draft(self):
        from core.sales_store import approve_draft, create_draft, get_lead, upsert_lead
        from services.sales_service import ingest_resend_event, send_approved

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, body for resend transport test.")
        approve_draft(draft["id"])

        class SendResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"id":"re_123"}'

        def fake_send(request, timeout):
            return SendResponse()

        with patch.dict(os.environ, {
            "SALES_RESEND_API_KEY": "re_test",
            "SALES_RESEND_DOMAIN": "resend-test.com",
            "SALES_FROM_EMAIL": "sales@example.com",
            "SALES_REPLY_TO_EMAIL": "reply@example.com",
        }, clear=False):
            with patch("urllib.request.urlopen", fake_send):
                send_approved(draft["id"])

        result = ingest_resend_event({
            "type": "email.delivered",
            "created_at": "2026-08-31T10:00:00Z",
            "data": {
                "email_id": "re_123",
                "from": "sales@resend-test.com",
                "to": ["ops@example.com"],
                "subject": "Operational records",
            },
        })
        stored = get_lead(lead["id"])
        self.assertTrue(result["matched"])
        self.assertEqual(stored["recent_events"][0]["event"], "resend.email.delivered")

    def test_resend_received_event_fetches_body_and_marks_reply(self):
        from core.sales_store import approve_draft, create_draft, get_lead, upsert_lead
        from services.sales_service import ingest_resend_event, send_approved

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, body for resend reply test.")
        approve_draft(draft["id"])

        class SendResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"id":"re_123"}'

        class ReceivedResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return b'{"id":"rx_123","from":"ops@example.com","to":["reply@example.com"],"subject":"Re: Operational records","text":"Please share the next step.","headers":{"in-reply-to":"re_123","references":"re_123"},"message_id":"<reply-3@example.com>","created_at":"2026-08-31T10:05:00Z"}'

        def fake_open(request, timeout):
            if request.full_url == "https://api.resend.com/emails":
                return SendResponse()
            if request.full_url == "https://api.resend.com/emails/receiving/rx_123":
                return ReceivedResponse()
            raise AssertionError(request.full_url)

        with patch.dict(os.environ, {
            "SALES_RESEND_API_KEY": "re_test",
            "SALES_RESEND_DOMAIN": "resend-test.com",
            "SALES_FROM_EMAIL": "sales@example.com",
            "SALES_REPLY_TO_EMAIL": "reply@example.com",
        }, clear=False):
            with patch("urllib.request.urlopen", fake_open):
                send_approved(draft["id"])
                result = ingest_resend_event({
                    "type": "email.received",
                    "created_at": "2026-08-31T10:05:00Z",
                    "data": {
                        "email_id": "rx_123",
                        "from": "ops@example.com",
                        "to": ["reply@example.com"],
                        "subject": "Re: Operational records",
                        "message_id": "<reply-3@example.com>",
                    },
                })
        stored = get_lead(lead["id"])
        self.assertEqual(result["event"], "email.received")
        self.assertEqual(stored["stage"], "replied")
        self.assertEqual(stored["interactions"][0]["provider_message_id"], "<reply-3@example.com>")
        self.assertEqual(stored["interactions"][0]["metadata"]["source"], "resend")

    def test_resend_send_failure_releases_send_claim(self):
        from core.sales_store import approve_draft, create_draft, get_draft, upsert_lead
        from services.sales_service import send_approved

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        draft = create_draft(lead["id"], "Operational records", "Hello, body for resend failure test.")
        approve_draft(draft["id"])

        with patch.dict(os.environ, {
            "SALES_RESEND_API_KEY": "re_test",
            "SALES_RESEND_DOMAIN": "resend-test.com",
            "SALES_FROM_EMAIL": "sales@example.com",
        }, clear=False):
            with patch("urllib.request.urlopen", side_effect=RuntimeError("network down")):
                with self.assertRaisesRegex(RuntimeError, "network down"):
                    send_approved(draft["id"])

        stored = get_draft(draft["id"])
        self.assertEqual(stored["status"], "approved")

    def test_inbound_email_ingest_marks_reply_and_preserves_metadata(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import ingest_inbound_email

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        result = ingest_inbound_email({
            "from_email": "Ops <ops@example.com>",
            "to_email": "sales@example.com",
            "subject": "Re: Operational records",
            "text": "Please share the next step.",
            "message_id": "<reply-1@example.com>",
            "in_reply_to": "<sent-1@example.com>",
            "references": ["<sent-1@example.com>"],
            "received_at": "2026-08-28T12:00:00Z",
            "headers": {"X-Source": "cloudflare"},
        })
        stored = get_lead(lead["id"])
        interaction = stored["interactions"][0]
        self.assertEqual(result["action"], "replied")
        self.assertFalse(result["duplicate"])
        self.assertEqual(stored["stage"], "replied")
        self.assertEqual(interaction["provider_message_id"], "<reply-1@example.com>")
        self.assertIn("<sent-1@example.com>", interaction["metadata_json"])

    def test_duplicate_inbound_email_is_ignored(self):
        from core.sales_store import upsert_lead
        from services.sales_service import ingest_inbound_email

        upsert_lead({"email": "ops@example.com", "source": "website"})
        payload = {
            "from_email": "ops@example.com",
            "subject": "Re: Operational records",
            "text": "Please share the next step.",
            "message_id": "<reply-1@example.com>",
        }
        first = ingest_inbound_email(payload)
        second = ingest_inbound_email(payload)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])

    def test_unsubscribe_inbound_email_suppresses_lead(self):
        from core.sales_store import get_lead, upsert_lead
        from services.sales_service import ingest_inbound_email

        lead, _ = upsert_lead({"email": "ops@example.com", "source": "website"})
        result = ingest_inbound_email({
            "from_email": "ops@example.com",
            "subject": "unsubscribe",
            "text": "stop",
            "message_id": "<reply-2@example.com>",
        })
        stored = get_lead(lead["id"])
        self.assertEqual(result["action"], "suppressed")
        self.assertEqual(stored["stage"], "suppressed")


if __name__ == "__main__":
    unittest.main()
