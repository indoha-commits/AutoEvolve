from __future__ import annotations

import json
import re
from pydantic import ValidationError
from urllib.parse import urlparse
from uuid import uuid4

from .config import Settings
from .context import context_pack, generation_context
from .m0_bridge import record_draft
from .models import CampaignRequest, ConceptSet, ContentConcept, DraftCampaign, GenerationTrace, ResearchBrief, SourceEvidence
from .prompts import (concepts_prompt, concepts_revision_prompt, research_prompt, revision_prompt,
                      writer_prompt, writer_schema_repair_prompt)
from .providers import JsonModel, PageExtractor, SearXNGSearch, diverse_results
from .security import similarity
from .story import closure_normalize, normalize_story, normalize_writer_payload
from .store import CampaignStore
from .validation import CampaignValidator, rank_concept


class GrowthPipeline:
    def __init__(self, settings: Settings, store: CampaignStore, model: JsonModel,
                 search: SearXNGSearch, extractor: PageExtractor):
        self.settings, self.store, self.model, self.search, self.extractor = settings, store, model, search, extractor
        self.product_claims = settings.knowledge("product_claims.json")
        self.prohibited = settings.knowledge("prohibited_claims.json")
        self.narratives = settings.knowledge("narrative_registry.json")["narratives"]
        self.cta_registry = settings.knowledge("cta_registry.json")
        self.exemplars = settings.knowledge("approved_exemplars.json")

    def run(self, request: CampaignRequest) -> dict:
        campaign_id = f"camp_{uuid4().hex[:12]}"
        self.store.audit("campaign.started", request.model_dump(), campaign_id)
        research = self._research(request, campaign_id)
        research_source = ("disabled" if not request.fresh_research else
                           "fail_soft" if any("model unavailable" in item.lower() for item in research.uncertainties)
                           else "model")
        research_context = self._generation_research(research)
        guessed_narrative = self._closest_narrative(request.topic or "logistics document control")
        context = context_pack(self.store, self.exemplars, guessed_narrative)
        recent = context["usable_campaign_memory"] + [{"hook": hook} for hook in context["reserved_headlines"]]
        prompt_context = generation_context(context)
        system, user = concepts_prompt(request.model_dump(), research_context, self.product_claims,
                                       prompt_context, self.narratives)
        concepts_source = "model"
        try:
            concepts = ConceptSet.model_validate(
                self.model.complete_json("concepts", system, user, campaign_id=campaign_id)
            )
        except Exception as exc:
            self.store.audit("concepts.failed", {"error": f"{type(exc).__name__}: {exc}"}, campaign_id)
            concepts = self._fallback_concepts(request, guessed_narrative)
            concepts_source = "deterministic_fallback"
        concept_issues = self._concept_issues(concepts, request.objective)
        if concept_issues:
            self.store.audit("concepts.rewrite_requested", {"reasons": concept_issues}, campaign_id)
            try:
                system, user = concepts_revision_prompt(request.model_dump(), research_context, self.product_claims,
                                                        prompt_context, self.narratives, concepts.model_dump(mode="json"))
                concepts = ConceptSet.model_validate(
                    self.model.complete_json("concepts_repair", system, user, campaign_id=campaign_id)
                )
                concepts_source = "model_repair"
                remaining_issues = self._concept_issues(concepts, request.objective)
                if remaining_issues:
                    self.store.audit("concepts.fallback", {"reasons": remaining_issues}, campaign_id)
                    concepts = self._fallback_concepts(request, guessed_narrative)
                    concepts_source = "deterministic_fallback"
            except Exception as exc:
                self.store.audit("concepts.rewrite_failed", {"error": f"{type(exc).__name__}: {exc}"}, campaign_id)
                concepts = self._fallback_concepts(request, guessed_narrative)
                concepts_source = "deterministic_fallback"
        for concept in concepts.concepts:
            if concept.narrative not in self.narratives:
                concept.narrative = self._closest_narrative(" ".join([concept.narrative, concept.pain, concept.thesis, concept.hook]))
            concept.cta = "trial" if request.objective == "trial" else "walkthrough"
        eligible = [concept for concept in concepts.concepts if self._eligible(concept)]
        selection_warning = None
        if not eligible:
            eligible = concepts.concepts
            selection_warning = "no concept passed the buyer, product-fit and evidence eligibility gate"
        selected = max(eligible, key=lambda concept: rank_concept(concept, recent))
        context = context_pack(self.store, self.exemplars, selected.narrative)
        prompt_context = generation_context(context)
        system, user = writer_prompt(request.model_dump(), selected.model_dump(), research_context,
                                     self.product_claims, prompt_context)
        writer_fallback_warning = None
        writer_source = "model"
        try:
            draft_data = self.model.complete_json("writer", system, user, campaign_id=campaign_id, writing=True)
        except Exception as exc:
            writer_source = "provider_fallback"
            writer_fallback_warning = "writer model unavailable; deterministic review draft used"
            self.store.audit("writer.provider_failed", {"error": f"{type(exc).__name__}: {exc}"}, campaign_id)
            draft = self._fallback_draft(request, selected, campaign_id)
            structural_actions = []
        else:
            draft_data.update({"campaign_id": campaign_id, "objective": request.objective,
                               "format": "carousel", "platforms": request.platforms,
                               "buyer": selected.buyer, "narrative": selected.narrative})
            draft_data, structural_actions = normalize_writer_payload(draft_data)
            if structural_actions:
                writer_source = "model_structural_normalization"
                self.store.audit("writer.structure_normalized", {"actions": structural_actions}, campaign_id)
            try:
                draft = DraftCampaign.model_validate(draft_data)
            except ValidationError as schema_error:
                self.store.audit("writer.schema_invalid", {"error": str(schema_error)}, campaign_id)
                try:
                    schema_system, schema_user = writer_schema_repair_prompt(
                        request.model_dump(), selected.model_dump(), research_context,
                        self.product_claims, prompt_context, draft_data, str(schema_error),
                    )
                    repaired_data = self.model.complete_json(
                        "writer_schema_repair", schema_system, schema_user,
                        campaign_id=campaign_id, writing=True,
                    )
                    repaired_data.update({"campaign_id": campaign_id, "objective": request.objective,
                                          "format": "carousel", "platforms": request.platforms,
                                          "buyer": selected.buyer, "narrative": selected.narrative})
                    repaired_data, repair_structure_actions = normalize_writer_payload(repaired_data)
                    draft = DraftCampaign.model_validate(repaired_data)
                    structural_actions = list(dict.fromkeys(
                        structural_actions + repair_structure_actions + ["writer_schema_repaired"]
                    ))
                    writer_source = "model_schema_repair"
                    self.store.audit("writer.schema_repaired", {"actions": structural_actions}, campaign_id)
                except Exception as repair_error:
                    writer_source = "invalid_output_fallback"
                    writer_fallback_warning = "writer output remained schema-invalid; deterministic review draft used"
                    structural_actions = []
                    self.store.audit("writer.schema_repair_failed", {
                        "error": f"{type(repair_error).__name__}: {repair_error}"
                    }, campaign_id)
                    draft = self._fallback_draft(request, selected, campaign_id)
        draft, closure_actions = closure_normalize(
            draft, self.cta_registry, self.product_claims, self.prohibited
        )
        deterministic_actions = list(dict.fromkeys(structural_actions + closure_actions))
        draft.generation = GenerationTrace(
            research=research_source, concepts=concepts_source, writer=writer_source,
            deterministic_actions=deterministic_actions,
        )
        approved = {item["id"] for item in self.product_claims["approved"]}
        approved.update(claim.id for claim in research.claims if claim.status == "supported")
        validator = CampaignValidator(approved, self.prohibited, recent, set(self.narratives))
        package = validator.validate(draft)
        if package.status == "needs_founder_review":
            self.store.audit("campaign.rewrite_requested", {"violations": package.quality.warnings}, campaign_id)
            try:
                system, user = revision_prompt(request.model_dump(), selected.model_dump(), research_context,
                                               self.product_claims, prompt_context, draft.model_dump(mode="json"),
                                               package.quality.warnings)
                repaired_data = self.model.complete_json("writer_repair", system, user,
                                                         campaign_id=campaign_id, writing=True)
                repaired_data.update({"campaign_id": campaign_id, "objective": request.objective,
                                      "format": "carousel", "platforms": request.platforms,
                                      "buyer": selected.buyer, "narrative": selected.narrative})
                repaired_data, editorial_structure_actions = normalize_writer_payload(repaired_data)
                draft = DraftCampaign.model_validate(repaired_data)
                writer_source = "model_repair"
                draft, repair_actions = closure_normalize(
                    draft, self.cta_registry, self.product_claims, self.prohibited
                )
                deterministic_actions = list(dict.fromkeys(
                    deterministic_actions + editorial_structure_actions + repair_actions
                ))
                draft.generation = GenerationTrace(
                    research=research_source, concepts=concepts_source, writer=writer_source,
                    deterministic_actions=deterministic_actions,
                )
                package = validator.validate(draft, rewrite_count=1)
                writer_fallback_warning = None
            except Exception as exc:
                package.quality.rewrite_count = 1
                package.quality.editorial_score = min(package.quality.editorial_score, 0.5)
                package.quality.warnings.append("bounded rewrite failed; founder review required")
                package.quality.warnings = sorted(set(package.quality.warnings))
                self.store.audit("campaign.rewrite_failed", {"error": f"{type(exc).__name__}: {exc}"}, campaign_id)
        if selection_warning:
            package.status = "needs_founder_review"
            package.quality.editorial_score = min(package.quality.editorial_score, 0.6)
            package.quality.warnings = sorted(set(package.quality.warnings + [selection_warning]))
        if writer_fallback_warning:
            package.status = "needs_founder_review"
            package.quality.editorial_score = min(package.quality.editorial_score, 0.6)
            package.quality.warnings = sorted(set(
                package.quality.warnings + [writer_fallback_warning]
            ))
        data = package.model_dump(mode="json")
        self.store.save(data, selected.hook)
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        output = self.settings.output_dir / f"{campaign_id}.json"
        output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        m0 = record_draft(data)
        self.store.audit("campaign.completed", {"status": package.status, "output": str(output), "m0": m0}, campaign_id)
        return {"campaign": data, "concepts": [item.model_dump() for item in concepts.concepts],
                "selected_concept": selected.model_dump(), "output": str(output), "m0": m0}

    def _closest_narrative(self, text: str) -> str:
        words = set(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())
        def score(candidate: str) -> tuple[int, int]:
            tokens = set(candidate.split("_"))
            return len(words & tokens), -self.narratives.index(candidate)
        return max(self.narratives, key=score)

    @staticmethod
    def _generation_research(research: ResearchBrief) -> dict:
        data = research.model_dump(mode="json")
        data["claims"] = [claim for claim in data["claims"] if claim["status"] == "supported"]
        supported_ids = {source_id for claim in data["claims"] for source_id in claim["source_ids"]}
        data["sources"] = [source for source in data["sources"] if source["id"] in supported_ids]
        return data

    @staticmethod
    def _concept_issues(concepts: ConceptSet, objective: str) -> list[str]:
        pains = [re.sub(r"[^a-z0-9]+", " ", item.pain.lower()).strip() for item in concepts.concepts]
        theses = [re.sub(r"[^a-z0-9]+", " ", item.thesis.lower()).strip() for item in concepts.concepts]
        issues: list[str] = []
        if len(set(pains)) < len(pains) or len(set(theses)) < len(theses):
            issues.append("duplicate pain or thesis")
        thesis_similarity = max((similarity(left, right) for index, left in enumerate(theses)
                                 for right in theses[index + 1:]), default=0.0)
        if thesis_similarity >= 0.70:
            issues.append("theses are near-duplicates")
        expected_cta = "trial" if objective == "trial" else "walkthrough"
        if any(item.cta != expected_cta for item in concepts.concepts):
            issues.append("concept CTA conflicts with campaign objective")
        if any("tired of" in item.hook.lower() or "?" in item.hook for item in concepts.concepts):
            issues.append("generic or question-based concept hook")
        if not any(GrowthPipeline._eligible(item) for item in concepts.concepts):
            issues.append("no concept passes eligibility thresholds")
        return issues

    @staticmethod
    def _eligible(concept) -> bool:
        return concept.buyer_relevance >= 0.80 and concept.product_fit >= 0.80 and concept.evidence_strength >= 0.70

    @staticmethod
    def _fallback_concepts(request: CampaignRequest, narrative: str) -> ConceptSet:
        buyer = request.buyer or "ops_manager"
        cta = "trial" if request.objective == "trial" else "walkthrough"
        rows = [
            ("fallback_workflow", "Different document copies move through separate operational handoffs.",
             "Uploaded documents can remain linked to the cargo record.", "HANDOFFS NEED ONE CURRENT RECORD",
             ["product.document_upload", "product.control_layer"]),
            ("fallback_accountability", "Teams need clear ownership of document validation and updates.",
             "Validation states and audit history make document handling visible.", "VERSION OWNERSHIP NEEDS A TRAIL",
             ["product.document_validation", "product.audit_trail"]),
            ("fallback_visibility", "Internal teams and clients need different views of the same cargo movement.",
             "Internal and client dashboards provide separate shipment views.", "CARGO RECORDS NEED SHARED CONTEXT",
             ["product.internal_dashboard", "product.client_dashboard", "product.milestones"]),
        ]
        return ConceptSet(concepts=[
            ContentConcept(id=id_, buyer=buyer, pain=pain, thesis=thesis, hook=hook, narrative=narrative,
                           cta=cta, allowed_claim_ids=claim_ids, evidence_strength=.8,
                           buyer_relevance=.88, product_fit=.88)
            for id_, pain, thesis, hook, claim_ids in rows
        ])

    @staticmethod
    def _fallback_draft(request: CampaignRequest, concept: ContentConcept, campaign_id: str) -> DraftCampaign:
        slides = [
            {"number":1,"purpose":"cover","headline":"DOCUMENTS MOVE BETWEEN TEAMS",
             "body":"One cargo record may be handled through several operational channels.",
             "visual_concept":"Freight operator and cargo documents at a working desk.",
             "asset_strategy":"stock_photo","image_queries":["East Africa freight operator documents"],"claim_ids":[]},
            {"number":2,"purpose":"evidence","headline":"COPIES NEED CLEAR OWNERSHIP",
             "body":"Email, messaging and operations folders can contain separate document copies.",
             "visual_concept":"Three document copies arranged across separate channels.",
             "asset_strategy":"document_composite","image_queries":["shipping paperwork office desk"],"claim_ids":[]},
            {"number":3,"purpose":"operational_complexity","headline":"EACH HANDOFF USES A RECORD",
             "body":"Operations, documentation and clearing teams work around the same cargo movement.",
             "visual_concept":"Three operational roles connected around one cargo identity.",
             "asset_strategy":"route_diagram","image_queries":["freight operations team East Africa"],"claim_ids":[]},
            {"number":4,"purpose":"failure_point","headline":"WHICH VERSION IS CURRENT?",
             "body":"The next operator first identifies the current file.",
             "visual_concept":"Two dated document versions compared side by side.",
             "asset_strategy":"document_composite","image_queries":["two shipping document copies desk"],"claim_ids":[]},
            {"number":5,"purpose":"control_system","headline":"LINK DOCUMENTS TO THE CARGO RECORD",
             "body":"Example Company allows documents to be uploaded and linked to cargo records.",
             "visual_concept":"Approved Example Company product UI showing a cargo record and documents.",
             "asset_strategy":"product_ui","image_queries":[],"claim_ids":["product.document_upload"]},
            {"number":6,"purpose":"outcome_cta","headline":"BOOK A WALKTHROUGH",
             "body":"Book a walkthrough.","visual_concept":"Official Example Company branded end card.",
             "asset_strategy":"branded_end_card","image_queries":[],"claim_ids":["offer.walkthrough"]},
        ]
        return DraftCampaign(campaign_id=campaign_id, objective=request.objective, buyer=concept.buyer,
                             narrative=concept.narrative, platforms=request.platforms,
                             claim_ids=["product.document_upload"], slides=slides,
                             platform_copy={"instagram_caption":"Example Company links uploaded documents to cargo records. Book a walkthrough.",
                                            "x_post":"Example Company links uploaded documents to cargo records. Book a walkthrough."})

    def _research(self, request: CampaignRequest, campaign_id: str) -> ResearchBrief:
        topic = request.topic or "logistics document control East Africa"
        if not request.fresh_research:
            return ResearchBrief(question=topic, claims=[], sources=[], uncertainties=["Fresh research disabled."])
        queries = [topic, f"site:unctad.org {topic}", f"site:wto.org {topic}", f"site:worldbank.org {topic}"]
        results = []
        seen = set()
        for query in queries:
            for row in self.search.search(query, 5):
                if row.url not in seen:
                    results.append(row); seen.add(row.url)
        evidence = []
        failed_domains: set[str] = set()
        for row in diverse_results(results, limit=12, per_domain=2):
            if len(evidence) >= 5:
                break
            host = (urlparse(row.url).hostname or "").lower().removeprefix("www.")
            domain = ".".join(host.split(".")[-2:])
            if domain in failed_domains:
                continue
            evidence_id = f"source_{len(evidence) + 1}"
            try:
                evidence.append(self.extractor.extract(row, evidence_id))
            except Exception as exc:
                failed_domains.add(domain)
                self.store.audit("source.failed", {"url": row.url, "error": f"{type(exc).__name__}: {exc}"}, campaign_id)
                if len(row.snippet.strip()) >= 100:
                    evidence.append(SourceEvidence(id=evidence_id, title=row.title, url=row.url,
                                                   authority=min(row.authority, 0.65),
                                                   source_class="search_result_excerpt",
                                                   excerpt=row.snippet.strip()))
        system, user = research_prompt(topic, [item.model_dump(mode="json") for item in evidence])
        try:
            data = self.model.complete_json("research", system, user, campaign_id=campaign_id)
        except Exception as exc:
            self.store.audit("research.model_failed", {"error": f"{type(exc).__name__}: {exc}"}, campaign_id)
            return ResearchBrief(question=topic, claims=[], sources=evidence,
                                 uncertainties=["Research model unavailable; external claims disabled for this campaign."])
        data["sources"] = [item.model_dump(mode="json") for item in evidence]
        brief = ResearchBrief.model_validate(data)
        valid_sources = {item.id for item in evidence}
        for claim in brief.claims:
            if claim.status == "supported" and (not claim.source_ids or not set(claim.source_ids).issubset(valid_sources)):
                claim.status = "unsupported"
                brief.uncertainties.append(f"Claim {claim.id} referenced missing evidence and was downgraded.")
        return brief
