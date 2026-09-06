from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from g1_runtime.config import Settings
from g1_runtime.context import context_pack, generation_context
from g1_runtime.models import CampaignRequest, ContentConcept, DraftCampaign, SearchResult, SourceEvidence
from g1_runtime.pipeline import GrowthPipeline
from g1_runtime.providers import OmniRouteModel, PageExtractor, authority_for, canonical_url, diverse_results
from g1_runtime.security import similarity
from g1_runtime.store import CampaignStore
from g1_runtime.story import closure_normalize, normalize_story, normalize_writer_payload
from g1_runtime.validation import CampaignValidator, rank_concept


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def test_default_models_are_known_omniroute_combo(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.load(ROOT)
        self.assertEqual(settings.reasoning_model, "auto/best-reasoning")
        self.assertEqual(settings.writing_model, "auto/best-chat")
        self.assertEqual(settings.fallback_model, "auto/best-reasoning")

    def test_authority(self):
        self.assertEqual(authority_for("https://unctad.org/news/test")[0], 1.0)
        self.assertLess(authority_for("https://blogs.worldbank.org/test")[0], 0.9)
        self.assertLess(authority_for("https://random.example/test")[0], 0.5)

    def test_canonical_url(self):
        self.assertEqual(canonical_url("https://Example.com/a/?utm_source=x&b=2#x"), "https://example.com/a?b=2")

    def test_similarity(self):
        self.assertEqual(similarity("ONE SHIPMENT MANY BORDERS", "ONE SHIPMENT MANY BORDERS"), 1.0)

    def test_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "g1.sqlite3"); store.init()
            package = {"campaign_id":"camp_x","status":"ready_for_media","buyer":"ops_manager","narrative":"test","objective":"awareness"}
            store.save(package, "new hook")
            self.assertEqual(store.recent()[0]["hook"], "new hook")

    def test_context_separates_usable_campaigns_from_failure_lessons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "g1.sqlite3"); store.init()
            ready = {"campaign_id":"camp_ready","status":"ready_for_media","buyer":"ops_manager",
                     "narrative":"outdated_document_versions","objective":"awareness",
                     "quality":{"warnings":[]}}
            rejected = {"campaign_id":"camp_bad","status":"needs_founder_review","buyer":"ops_manager",
                        "narrative":"outdated_document_versions","objective":"awareness",
                        "quality":{"warnings":["slide 2 has ungrounded claim language: delays",
                                               "slide 3 has ungrounded claim language: errors"]}}
            store.save(ready, "APPROVED HOOK")
            store.save(rejected, "REJECTED HOOK")
            exemplars = {"style_principles":[],"reserved_headlines":[],"campaigns":[]}
            context = context_pack(store, exemplars, "outdated_document_versions")
            self.assertEqual([item["hook"] for item in context["usable_campaign_memory"]], ["APPROVED HOOK"])
            self.assertEqual(context["failure_lessons"][0]["avoid"],
                             "causal or outcome claims without sentence-level evidence")
            self.assertEqual(context["failure_lessons"][0]["occurrences"], 2)

    def test_generation_context_hides_exact_reserved_copy(self):
        context = {
            "style_principles":["use operational contrasts"],
            "positive_exemplar":{"id":"golden","narrative":"outdated_document_versions","buyer":"ops_manager",
                                 "objective":"awareness","story_anatomy":["cargo and record diverge"],
                                 "visual_strategy":["stock_photo"],"voice_reference":["PROTECTED EXACT COPY"]},
            "reserved_headlines":["PROTECTED EXACT COPY"],
            "usable_campaign_memory":[],"failure_lessons":[],
        }
        prompt_context = generation_context(context)
        serialized = json.dumps(prompt_context)
        self.assertNotIn("PROTECTED EXACT COPY", serialized)
        self.assertNotIn("reserved_headlines", prompt_context)
        self.assertIn("cargo and record diverge", serialized)

    def test_pdf_url_uses_pdf_extractor(self):
        class RoutingExtractor(PageExtractor):
            def _pdf(self, url):
                return "Authoritative PDF text " * 10
            async def _crawl(self, url):
                raise AssertionError("PDF must not use Crawl4AI")
        row = SearchResult(title="Report", url="https://wto.org/report.PDF", snippet="", authority=1,
                           source_class="primary_report")
        with patch("g1_runtime.providers.safe_public_url"):
            evidence = RoutingExtractor().extract(row, "source_1")
        self.assertIn("Authoritative PDF text", evidence.excerpt)

    def test_extensionless_pdf_uses_pdf_extractor(self):
        class RoutingExtractor(PageExtractor):
            def _remote_is_pdf(self, url):
                return True
            def _pdf(self, url):
                return "Extensionless authoritative PDF text " * 10
            async def _crawl(self, url):
                raise AssertionError("Detected PDF must not use Crawl4AI")
        row = SearchResult(title="Report", url="https://documents.worldbank.org/bitstream/record/12345",
                           snippet="", authority=1, source_class="primary_report")
        with patch("g1_runtime.providers.safe_public_url"):
            evidence = RoutingExtractor().extract(row, "source_1")
        self.assertIn("Extensionless authoritative PDF", evidence.excerpt)

    def test_research_route_has_one_bounded_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "g1.sqlite3"); store.init()
            model = OmniRouteModel("http://127.0.0.1:1/v1", "test", "reasoning", "writing",
                                   "fallback", store, timeouts=(1, 2))
            attempts = []
            def fail(model_name, system, user, timeout):
                attempts.append((model_name, timeout))
                raise TimeoutError("simulated")
            model._request = fail
            with self.assertRaises(RuntimeError):
                model.complete_json("research", "system", "user", campaign_id="camp_x")
            self.assertEqual(attempts, [("reasoning", 1)])

    def test_model_run_records_actual_routed_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "g1.sqlite3"); store.init()
            store.model_run("camp_x", "writer", "auto/best-reasoning", 1, "pass", 12,
                            {"total_tokens": 10}, actual_model="provider/model-v2")
            with store.connect() as conn:
                row = conn.execute("SELECT model,actual_model FROM model_runs").fetchone()
            self.assertEqual(row["model"], "auto/best-reasoning")
            self.assertEqual(row["actual_model"], "provider/model-v2")

    def test_existing_database_migrates_model_telemetry_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g1.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE model_runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT, stage TEXT NOT NULL,
              model TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
              latency_ms INTEGER NOT NULL, usage_json TEXT, error TEXT, created_at TEXT NOT NULL
            )""")
            conn.commit(); conn.close()
            store = CampaignStore(path); store.init()
            with store.connect() as migrated:
                columns = {row[1] for row in migrated.execute("PRAGMA table_info(model_runs)")}
            self.assertIn("actual_model", columns)

    def test_diverse_results_caps_each_domain(self):
        rows = [
            SearchResult(title=f"UNCTAD {i}", url=f"https://unctad.org/{i}", snippet="x", authority=1,
                         source_class="primary_institution") for i in range(4)
        ] + [
            SearchResult(title="WTO", url="https://wto.org/a", snippet="x", authority=1,
                         source_class="primary_institution"),
            SearchResult(title="World Bank", url="https://worldbank.org/a", snippet="x", authority=.92,
                         source_class="authoritative_institution"),
        ]
        selected = diverse_results(rows, limit=5, per_domain=2)
        self.assertEqual(sum("unctad.org" in item.url for item in selected), 2)
        self.assertTrue(any("wto.org" in item.url for item in selected))
        self.assertTrue(any("worldbank.org" in item.url for item in selected))

    def test_rank_penalizes_duplicate(self):
        concept = ContentConcept(id="c", buyer="ops", pain="p", thesis="t", hook="same hook", narrative="n", cta="walkthrough",
                                 allowed_claim_ids=[], evidence_strength=1, buyer_relevance=1, product_fit=1)
        self.assertLess(rank_concept(concept, [{"hook":"same hook"}]), 0.8)

    def test_concept_eligibility_precedes_novelty(self):
        strong = ContentConcept(id="strong", buyer="ops", pain="handoff", thesis="current record", hook="KNOWN HOOK",
                                narrative="outdated_document_versions", cta="walkthrough", allowed_claim_ids=[],
                                evidence_strength=.8, buyer_relevance=.9, product_fit=.9)
        novel_but_weak = ContentConcept(id="weak", buyer="ops", pain="warehouse", thesis="generic efficiency",
                                        hook="NOVEL HOOK", narrative="outdated_document_versions", cta="walkthrough",
                                        allowed_claim_ids=[], evidence_strength=.6, buyer_relevance=.7, product_fit=.6)
        self.assertTrue(GrowthPipeline._eligible(strong))
        self.assertFalse(GrowthPipeline._eligible(novel_but_weak))

    def test_forbidden_and_wrong_offer_block(self):
        with open(ROOT / "knowledge/product_claims.json", encoding="utf-8") as stream:
            claims = json.load(stream)
        with open(ROOT / "knowledge/prohibited_claims.json", encoding="utf-8") as stream:
            prohibited = json.load(stream)
        draft = DraftCampaign(campaign_id="camp_test", objective="awareness", buyer="ops_manager", narrative="test", platforms=["instagram","x"], claim_ids=[],
            slides=[
              {"number":1,"purpose":"cover","headline":"A REAL OPERATIONS PROBLEM","body":"Example Logistics appears here.","visual_concept":"truck","image_queries":[],"claim_ids":[]},
              {"number":2,"purpose":"evidence","headline":"CURRENT RECORDS MATTER","body":"Evidence.","visual_concept":"docs","image_queries":[],"claim_ids":[]},
              {"number":3,"purpose":"operational_complexity","headline":"TEAMS SHARE THE CARGO","body":"Handoffs.","visual_concept":"team","image_queries":[],"claim_ids":[]},
              {"number":4,"purpose":"failure_point","headline":"OLD DATA CREATES RISK","body":"Mismatch.","visual_concept":"warning","image_queries":[],"claim_ids":[]},
              {"number":5,"purpose":"control_system","headline":"ONE CONTROL LAYER","body":"Control.","visual_concept":"trail","image_queries":[],"claim_ids":[]}
            ], platform_copy={"instagram_caption":"Start your 14-day trial.","x_post":"test"})
        approved = {item["id"] for item in claims["approved"]}
        result = CampaignValidator(approved, prohibited, []).validate(draft)
        self.assertEqual(result.status, "needs_founder_review")
        self.assertTrue(result.quality.forbidden_terms)

    def test_live_generic_campaign_is_blocked(self):
        with open(ROOT / "knowledge/product_claims.json", encoding="utf-8") as stream:
            claims = json.load(stream)
        with open(ROOT / "knowledge/prohibited_claims.json", encoding="utf-8") as stream:
            prohibited = json.load(stream)
        with open(ROOT / "knowledge/narrative_registry.json", encoding="utf-8") as stream:
            narratives = set(json.load(stream)["narratives"])
        draft = DraftCampaign(
            campaign_id="camp_live_failure", objective="awareness", buyer="ops_manager",
            narrative="outdated_document_versions", platforms=["instagram", "x"],
            claim_ids=["product.control_layer"],
            slides=[
                {"number":1,"purpose":"cover","headline":"Tired of outdated shipping documents?","body":"Example Company helps manage shipping documents efficiently.","visual_concept":"team","image_queries":["logistics team using Example Company"],"claim_ids":[]},
                {"number":2,"purpose":"evidence","headline":"Outdated documents cause delays","body":"Outdated documents lead to costly errors and delays.","visual_concept":"port","image_queries":["ship delayed due to outdated shipping documents"],"claim_ids":[]},
                {"number":3,"purpose":"operational_complexity","headline":"Managing documents is time consuming","body":"Teams manage many versions.","visual_concept":"desk","image_queries":["freight office document desk"],"claim_ids":[]},
                {"number":4,"purpose":"failure_point","headline":"Errors disrupt freight operations","body":"Errors result in supply chain disruptions.","visual_concept":"plane","image_queries":["cargo plane delayed due to documents"],"claim_ids":[]},
                {"number":5,"purpose":"control_system","headline":"Example Company helps manage every record","body":"Example Company provides one control layer.","visual_concept":"dashboard","image_queries":["logistics team using Example Company"],"claim_ids":["product.control_layer"]},
                {"number":6,"purpose":"outcome_cta","headline":"Improve efficiency with Example Company","body":"Book a walkthrough. Start your 7-day trial today.","visual_concept":"team","image_queries":["example.com"],"claim_ids":["offer.walkthrough", "offer.trial_7_day"]},
            ],
            platform_copy={"instagram_caption":"Tired of delays? Learn more. Book a walkthrough.",
                           "x_post":"Improve efficiency. Book a walkthrough."},
        )
        approved = {item["id"] for item in claims["approved"]}
        result = CampaignValidator(approved, prohibited, [], narratives).validate(draft)
        self.assertEqual(result.status, "needs_founder_review")
        self.assertFalse(result.quality.all_claims_grounded)
        joined = "\n".join(result.quality.warnings)
        self.assertIn("awareness campaign cannot use the trial CTA", joined)
        self.assertIn("generic hook", joined)
        self.assertIn("ungrounded claim language", joined)
        self.assertIn("image query is not photographable", joined)

    def test_five_slide_story_normalization_resolves_structural_contradiction(self):
        with open(ROOT / "knowledge/cta_registry.json", encoding="utf-8") as stream:
            ctas = json.load(stream)
        draft = DraftCampaign(
            campaign_id="camp_structure", objective="awareness", buyer="ops_manager",
            narrative="outdated_document_versions", platforms=["instagram", "x"], claim_ids=[],
            slides=[
                {"number":1,"purpose":"cover","headline":"DOCUMENT VERSIONS","body":"Records pass between desks.","visual_concept":"desk","image_queries":["freight office desk"],"claim_ids":[]},
                {"number":2,"purpose":"evidence","headline":"DOCUMENT HANDOFFS","body":"Teams receive different files.","visual_concept":"handoff","image_queries":["document handoff freight office"],"claim_ids":[]},
                {"number":3,"purpose":"operational_complexity","headline":"RECORDS SPLIT","body":"The next desk checks the file.","visual_concept":"files","image_queries":["shipping files on table"],"claim_ids":[]},
                {"number":4,"purpose":"failure_point","headline":"CONTROL LAYER","body":"Example Company links documents to cargo records.","visual_concept":"dashboard","image_queries":["cargo dashboard screen"],"claim_ids":["product.document_upload"]},
                {"number":5,"purpose":"control_system","headline":"BOOK A WALKTHROUGH","body":"Example Company helps manage files. Book a walkthrough.","visual_concept":"team","image_queries":["logistics team at computer"],"claim_ids":["offer.walkthrough"]},
            ], platform_copy={"instagram_caption":"Start your 7-day trial. Learn more.",
                              "x_post":"Start your 7-day trial today."}
        )
        normalized = normalize_story(draft, ctas)
        self.assertEqual([slide.purpose for slide in normalized.slides],
                         ["cover", "operational_complexity", "failure_point", "control_system", "outcome_cta"])
        self.assertEqual([slide.asset_strategy for slide in normalized.slides],
                         ["stock_photo", "route_diagram", "document_composite", "product_ui", "branded_end_card"])
        self.assertIn("Example Company", normalized.slides[3].body)
        self.assertEqual(normalized.slides[3].image_queries, [])
        self.assertEqual(normalized.slides[4].image_queries, [])
        self.assertEqual(normalized.slides[4].body, ctas["awareness"]["copy"])
        self.assertEqual(normalized.slides[4].headline, "BOOK A WALKTHROUGH")
        self.assertEqual(normalized.slides[4].claim_ids, ["offer.walkthrough"])
        self.assertNotIn("trial", normalized.platform_copy.instagram_caption.lower())
        self.assertNotIn("trial", normalized.platform_copy.x_post.lower())
        self.assertIn("Book a walkthrough", normalized.platform_copy.instagram_caption)
        self.assertEqual(normalized.platform_copy.instagram_caption.count("See how Example Company fits your operation"), 1)

    def test_closure_normalization_resolves_observed_live_defects(self):
        with open(ROOT / "knowledge/cta_registry.json", encoding="utf-8") as stream:
            ctas = json.load(stream)
        with open(ROOT / "knowledge/product_claims.json", encoding="utf-8") as stream:
            claims = json.load(stream)
        with open(ROOT / "knowledge/prohibited_claims.json", encoding="utf-8") as stream:
            prohibited = json.load(stream)
        draft = DraftCampaign(
            campaign_id="camp_closure", objective="awareness", buyer="ops_manager",
            narrative="outdated_document_versions", platforms=["instagram", "x"],
            claim_ids=["product.control_layer", "product.internal_dashboard", "product.client_dashboard"],
            slides=[
                {"number":1,"purpose":"cover","headline":"VISIBILITY INTO OUTDATED DOCUMENTS",
                 "body":"Physical shipments and digital records move at different speeds, causing mismatches.",
                 "visual_concept":"ship and digital document","image_queries":["cargo ship"],"claim_ids":[]},
                {"number":2,"purpose":"operational_complexity","headline":"SAME SHIPMENT APPEARS AS DIFFERENT FILES",
                 "body":"The same shipment may appear across channels, leading to confusion.",
                 "visual_concept":"documents","image_queries":["shipping documents"],"claim_ids":[]},
                {"number":3,"purpose":"failure_point","headline":"MULTIPLE ROLES DEPEND ON ONE CURRENT RECORD",
                 "body":"Multiple roles depend on one record, which can be outdated or incorrect.",
                 "visual_concept":"documents","image_queries":["shipping documents"],"claim_ids":[]},
                {"number":4,"purpose":"control_system","headline":"UNAPPROVED BRAND PROVIDES REAL-TIME VISIBILITY",
                 "body":"Example Company provides real-time visibility into document versions and cargo status.",
                 "visual_concept":"real-time dashboard","image_queries":[],
                 "claim_ids":["product.control_layer", "product.internal_dashboard"]},
                {"number":5,"purpose":"outcome_cta","headline":"BOOK A WALKTHROUGH",
                 "body":"Book a walkthrough.","visual_concept":"end card","image_queries":[],
                 "claim_ids":["offer.walkthrough"]},
            ],
            platform_copy={"instagram_caption":"Delays improve when records are current. Learn more. Book a walkthrough.",
                           "x_post":"Delays improve when records are current. Book a walkthrough."},
        )
        normalized, actions = closure_normalize(draft, ctas, claims, prohibited)
        approved = {item["id"] for item in claims["approved"]}
        narratives = {"outdated_document_versions"}
        package = CampaignValidator(approved, prohibited, [], narratives).validate(normalized)
        self.assertEqual(package.status, "ready_for_media")
        self.assertTrue(package.quality.all_claims_grounded)
        self.assertNotIn("real-time visibility", package.model_dump_json().lower())
        self.assertIn("example company", normalized.slides[-2].headline.lower())
        self.assertEqual(normalized.platform_copy.instagram_caption.count("Book a walkthrough"), 1)
        self.assertIn("grounded_control_system", actions)
        self.assertIn("grounded_platform_copy", actions)

    def test_seven_slide_writer_payload_is_reduced_to_canonical_six(self):
        purposes = ["cover", "evidence", "evidence", "operational_complexity",
                    "failure_point", "control_system", "outcome_cta"]
        payload = {"slides": [{"number": index, "purpose": purpose, "marker": index}
                              for index, purpose in enumerate(purposes, 1)]}
        normalized, actions = normalize_writer_payload(payload)
        self.assertEqual(len(normalized["slides"]), 6)
        self.assertEqual([slide["purpose"] for slide in normalized["slides"]],
                         ["cover", "evidence", "operational_complexity", "failure_point",
                          "control_system", "outcome_cta"])
        self.assertEqual([slide["number"] for slide in normalized["slides"]], list(range(1, 7)))
        self.assertEqual(actions, ["trimmed_writer_slides_7_to_6"])

    def test_pipeline_accepts_seven_slide_writer_via_structural_normalization(self):
        class SevenSlideModel:
            def complete_json(self, stage, system, user, **kwargs):
                if stage == "writer_schema_repair":
                    raise AssertionError("seven-slide payload should be normalized without another model call")
                if stage in {"concepts", "concepts_repair"}:
                    return {"concepts": [
                        {"id":f"c{i}","buyer":"ops_manager","pain":f"operational handoff {i}",
                         "thesis":f"cargo record view {i}","hook":f"RECORD OWNERSHIP PATH {i}",
                         "narrative":"outdated_document_versions","cta":"walkthrough",
                         "allowed_claim_ids":["product.document_upload"],"evidence_strength":.8,
                         "buyer_relevance":.9,"product_fit":.9} for i in range(1, 4)
                    ]}
                purposes = ["cover", "evidence", "evidence", "operational_complexity",
                            "failure_point", "control_system", "outcome_cta"]
                headlines = [
                    "RECORDS CROSS OPERATIONAL DESKS", "CHANNELS HOLD DOCUMENT COPIES",
                    "REDUNDANT SEVENTH SLIDE", "EACH ROLE USES THE RECORD",
                    "WHICH FILE IS CURRENT?", "LINK DOCUMENTS TO CARGO", "BOOK A WALKTHROUGH",
                ]
                bodies = [
                    "Cargo records move through several operational channels.",
                    "Email and operations folders may contain separate document copies.",
                    "This additional evidence slide is structurally redundant.",
                    "Operations and documentation teams each work around the cargo record.",
                    "The next operator first identifies which file is current.",
                    "Documents can be uploaded and linked to cargo records.",
                    "Book a walkthrough.",
                ]
                strategies = ["stock_photo", "document_composite", "document_composite", "route_diagram",
                              "document_composite", "product_ui", "branded_end_card"]
                slides = []
                for index, purpose in enumerate(purposes, 1):
                    slides.append({
                        "number": index, "purpose": purpose, "headline": headlines[index - 1],
                        "body": bodies[index - 1], "visual_concept": "Freight documents on an operations desk",
                        "asset_strategy": strategies[index - 1],
                        "image_queries": [] if index >= 6 else ["freight documents operations desk"],
                        "claim_ids": ["product.document_upload"] if index == 6 else [],
                    })
                return {
                    "campaign_id":"replaced", "objective":"awareness", "buyer":"ops_manager",
                    "narrative":"outdated_document_versions", "format":"carousel",
                    "platforms":["instagram", "x"], "claim_ids":["product.document_upload"],
                    "slides":slides,
                    "platform_copy":{"instagram_caption":"Book a walkthrough.", "x_post":"Book a walkthrough."},
                    "brand":{"canvas":"1080x1350","logo_asset":"company_core_logo",
                             "logo_variant":"white","logo_position":"top_left"},
                }

        class UnusedProvider:
            def search(self, *args, **kwargs):
                raise AssertionError("fresh research is disabled")
            def extract(self, *args, **kwargs):
                raise AssertionError("fresh research is disabled")

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(root=Path(tmp), knowledge_dir=ROOT / "knowledge", data_dir=Path(tmp)/"data",
                                output_dir=Path(tmp)/"outputs", searxng_base_url="http://127.0.0.1:8080",
                                omniroute_base_url="http://127.0.0.1:20128/v1", coding_api_key="test",
                                reasoning_model="test", writing_model="test", fallback_model="test")
            store = CampaignStore(settings.data_dir / "g1.sqlite3"); store.init()
            output = GrowthPipeline(settings, store, SevenSlideModel(), UnusedProvider(), UnusedProvider()).run(
                CampaignRequest(topic="documents", fresh_research=False)
            )
            campaign = output["campaign"]
            self.assertEqual(campaign["status"], "ready_for_media")
            self.assertEqual(len(campaign["slides"]), 6)
            self.assertEqual(campaign["generation"]["writer"], "model_structural_normalization")
            self.assertIn("trimmed_writer_slides_7_to_6", campaign["generation"]["deterministic_actions"])

    def test_offline_pipeline(self):
        class FakeModel:
            def complete_json(self, stage, system, user, **kwargs):
                if stage == "research":
                    return {"question":"document control","claims":[],"sources":[],"uncertainties":[]}
                if stage in {"concepts", "concepts_repair"}:
                    if stage == "concepts":
                        pains = ["outdated files"] * 3
                        theses = ["current records matter"] * 3
                    else:
                        pains = ["files split at handoff", "operators verify different copies", "cargo control loses context"]
                        theses = ["trace each document handoff", "make version ownership visible", "link records to one cargo trail"]
                    return {"concepts":[
                        {"id":f"c{i}","buyer":"ops_manager","pain":pains[i-1],"thesis":theses[i-1],
                         "hook":f"DISTINCT OPERATIONS HOOK {i}","narrative":"outdated_document_versions","cta":"walkthrough",
                         "allowed_claim_ids":["product.document_upload"],"evidence_strength":0.8,"buyer_relevance":0.9,"product_fit":0.9}
                        for i in range(1,4)]}
                slides = []
                purposes = ["cover","evidence","operational_complexity","failure_point","control_system","outcome_cta"]
                headlines = [
                    "THE RECORD LEFT FIRST", "DOCUMENTS CROSS EVERY DESK", "VERSIONS SPLIT BETWEEN TEAMS",
                    "THE NEXT HANDOFF BREAKS", "ONE CARGO TRAIL STAYS CURRENT", "MAKE EVERY HANDOFF VISIBLE",
                ]
                bodies = [
                    "A shipment keeps moving while its records pass between teams.",
                    "Bills of lading, invoices and packing lists reach different operators.",
                    "One desk can hold a newer file while another still uses the previous copy.",
                    "The mismatch becomes visible only when the next team checks the record.",
                    "Example Company links uploaded documents and milestones to the cargo record.",
                    "See how Example Company fits your operation. Book a walkthrough.",
                ]
                for i, purpose in enumerate(purposes, 1):
                    if stage == "writer":
                        headline = f"THE SAME DOCUMENT PROBLEM {i}"
                        body = "The same document problem appears at every freight handoff."
                    else:
                        headline, body = headlines[i - 1], bodies[i - 1]
                    if i == 6:
                        body = bodies[i - 1]
                    slides.append({"number":i,"purpose":purpose,"headline":headline,"body":body,
                                   "visual_concept":"real freight operation","image_queries":["East Africa freight office"],
                                   "claim_ids":["product.document_upload", "product.milestones"] if i == 5 else []})
                return {"campaign_id":"replaced","objective":"awareness","buyer":"ops_manager",
                        "narrative":"outdated_document_versions","format":"carousel","platforms":["instagram","x"],
                        "claim_ids":["product.document_upload"],"slides":slides,
                        "platform_copy":{"instagram_caption":"See how it fits. Book a walkthrough.","x_post":"Book a walkthrough."},
                        "brand":{"canvas":"1080x1350","logo_asset":"company_core_logo","logo_variant":"white","logo_position":"top_left"}}

        class FakeSearch:
            def search(self, query, limit=8):
                return [SearchResult(title="Official", url="https://unctad.org/test", snippet="Evidence", authority=1, source_class="primary_institution")]

        class FakeExtractor:
            def extract(self, row, evidence_id):
                return SourceEvidence(id=evidence_id,title=row.title,url=row.url,authority=row.authority,source_class=row.source_class,excerpt="Evidence")

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(root=Path(tmp), knowledge_dir=ROOT / "knowledge", data_dir=Path(tmp)/"data",
                                output_dir=Path(tmp)/"outputs", searxng_base_url="http://127.0.0.1:8080",
                                omniroute_base_url="http://127.0.0.1:20128/v1", coding_api_key="test",
                                reasoning_model="test", writing_model="test", fallback_model="test")
            store = CampaignStore(settings.data_dir / "g1.sqlite3"); store.init()
            output = GrowthPipeline(settings, store, FakeModel(), FakeSearch(), FakeExtractor()).run(CampaignRequest(topic="documents"))
            self.assertEqual(output["campaign"]["status"], "ready_for_media")
            self.assertEqual(output["campaign"]["quality"]["rewrite_count"], 1)
            self.assertEqual(len({item["pain"] for item in output["concepts"]}), 3)
            self.assertTrue(Path(output["output"]).exists())

    def test_failed_rewrite_preserves_founder_review_package(self):
        class FailingRepairModel:
            def complete_json(self, stage, system, user, **kwargs):
                if stage == "concepts":
                    return {"concepts":[
                        {"id":f"c{i}","buyer":"ops_manager","pain":"old files","thesis":"versions split",
                         "hook":f"DOCUMENT VERSIONS SPLIT {i}","narrative":"outdated_document_versions",
                         "cta":"walkthrough","allowed_claim_ids":[],"evidence_strength":0.5,
                         "buyer_relevance":0.9,"product_fit":0.9} for i in range(1,4)
                    ]}
                if stage == "writer_repair":
                    raise TimeoutError("simulated repair timeout")
                purposes = ["cover", "operational_complexity", "failure_point", "control_system", "outcome_cta"]
                return {
                    "campaign_id":"replaced", "objective":"awareness", "buyer":"ops_manager",
                    "narrative":"outdated_document_versions", "format":"carousel", "platforms":["instagram","x"],
                    "claim_ids":[],
                    "slides":[
                        {"number":i,"purpose":purpose,
                         "headline":"EXTERNAL EVIDENCE RECORD" if i == 2 else f"Tired of document problems {i}?",
                         "body":"An external source describes this workflow." if i == 2 else
                                "The same document problem causes delays at every handoff." if i < 5 else
                                "Book a walkthrough. Start your 7-day trial.",
                         "visual_concept":"freight office","image_queries":["ship delayed due to documents"],
                        "claim_ids":["claim.external_bad"] if i == 2 else []}
                        for i, purpose in enumerate(purposes, 1)
                    ],
                    "platform_copy":{"instagram_caption":"Tired of delays? Book a walkthrough.",
                                     "x_post":"Book a walkthrough."},
                    "brand":{"canvas":"1080x1350","logo_asset":"company_core_logo",
                             "logo_variant":"white","logo_position":"top_left"},
                }

        class UnusedProvider:
            def search(self, *args, **kwargs):
                raise AssertionError("research provider should not be called")
            def extract(self, *args, **kwargs):
                raise AssertionError("extractor should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(root=Path(tmp), knowledge_dir=ROOT / "knowledge", data_dir=Path(tmp)/"data",
                                output_dir=Path(tmp)/"outputs", searxng_base_url="http://127.0.0.1:8080",
                                omniroute_base_url="http://127.0.0.1:20128/v1", coding_api_key="test",
                                reasoning_model="test", writing_model="test", fallback_model="test")
            store = CampaignStore(settings.data_dir / "g1.sqlite3"); store.init()
            output = GrowthPipeline(settings, store, FailingRepairModel(), UnusedProvider(), UnusedProvider()).run(
                CampaignRequest(topic="documents", fresh_research=False)
            )
            campaign = output["campaign"]
            self.assertTrue(output["selected_concept"]["id"].startswith("fallback_"))
            self.assertEqual(campaign["status"], "needs_founder_review")
            self.assertEqual(campaign["quality"]["rewrite_count"], 1)
            self.assertIn("bounded rewrite failed; founder review required", campaign["quality"]["warnings"])
            self.assertTrue(Path(output["output"]).exists())

    def test_research_model_failure_degrades_without_external_claims(self):
        class FailingResearchModel:
            def complete_json(self, *args, **kwargs):
                raise RuntimeError("HTTP 503: combo unavailable")
        class OneSearch:
            def search(self, query, limit=8):
                return [SearchResult(title="Official report", url="https://wto.org/report", snippet="A" * 120,
                                     authority=1, source_class="primary_institution")]
        class OneExtractor:
            def extract(self, row, evidence_id):
                return SourceEvidence(id=evidence_id, title=row.title, url=row.url, authority=row.authority,
                                      source_class=row.source_class, excerpt="Verified source excerpt " * 10)
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(root=Path(tmp), knowledge_dir=ROOT / "knowledge", data_dir=Path(tmp)/"data",
                                output_dir=Path(tmp)/"outputs", searxng_base_url="http://127.0.0.1:8080",
                                omniroute_base_url="http://127.0.0.1:20128/v1", coding_api_key="test",
                                reasoning_model="test", writing_model="test", fallback_model="test")
            store = CampaignStore(settings.data_dir / "g1.sqlite3"); store.init()
            pipeline = GrowthPipeline(settings, store, FailingResearchModel(), OneSearch(), OneExtractor())
            brief = pipeline._research(CampaignRequest(topic="documents"), "camp_research_fail")
            self.assertEqual(brief.claims, [])
            self.assertEqual(len(brief.sources), 1)
            self.assertIn("external claims disabled", brief.uncertainties[0])

    def test_generation_provider_failure_returns_review_draft(self):
        class UnavailableModel:
            def complete_json(self, *args, **kwargs):
                raise RuntimeError("HTTP 503: provider pool unavailable")
        class UnusedProvider:
            def search(self, *args, **kwargs):
                raise AssertionError("fresh research is disabled")
            def extract(self, *args, **kwargs):
                raise AssertionError("fresh research is disabled")
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(root=Path(tmp), knowledge_dir=ROOT / "knowledge", data_dir=Path(tmp)/"data",
                                output_dir=Path(tmp)/"outputs", searxng_base_url="http://127.0.0.1:8080",
                                omniroute_base_url="http://127.0.0.1:20128/v1", coding_api_key="test",
                                reasoning_model="test", writing_model="test", fallback_model="test")
            store = CampaignStore(settings.data_dir / "g1.sqlite3"); store.init()
            output = GrowthPipeline(settings, store, UnavailableModel(), UnusedProvider(), UnusedProvider()).run(
                CampaignRequest(topic="documents", fresh_research=False)
            )
            self.assertEqual(output["campaign"]["status"], "needs_founder_review")
            self.assertEqual(output["campaign"]["generation"]["writer"], "provider_fallback")
            self.assertIn("writer model unavailable; deterministic review draft used",
                          output["campaign"]["quality"]["warnings"])
            self.assertTrue(Path(output["output"]).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
