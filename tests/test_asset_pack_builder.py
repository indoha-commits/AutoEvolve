import unittest

from services.asset_pack_builder import build_asset_package, build_pack_title, split_script_sections
from services.asset_scene_planner import (
    SceneBoundaryPlan,
    _boundary_error,
    _normalize_boundaries,
    _script_metrics,
    plan_script_scenes,
)
from unittest.mock import patch


class AssetPackBuilderTests(unittest.TestCase):
    def test_split_script_sections_ignores_bracket_markers(self):
        script = "[HOOK]\n\nWhere is the cargo?\n\n[CTA]\n\nBook the walkthrough now."
        sections = split_script_sections(script)
        self.assertEqual(sections, ["Where is the cargo?", "Book the walkthrough now."])

    def test_face_video_metadata_is_removed_but_short_spoken_beats_are_kept(self):
        script = (
            "EXAMPLE COMPANY - FACE VIDEO #1\n\n"
            "Target length: 90–120 seconds\n\n"
            "[HOOK]\n\n"
            "“Thirty seconds?\n\n"
            "Five minutes?\n\n"
            "Who knew?\n\n"
            "Example Company.”"
        )
        self.assertEqual(split_script_sections(script), [
            "“Thirty seconds?",
            "Five minutes?",
            "Who knew?",
            "Example Company.”",
        ])

    def test_build_asset_package_creates_g2_compatible_slides(self):
        package = build_asset_package(
            "Cargo updates sit in WhatsApp and email.\n\nClients still wait for answers.",
            objective="awareness",
            social_platforms=["instagram", "x"],
            video_platform="shorts",
        )
        self.assertEqual(package["status"], "ready_for_media")
        self.assertEqual(package["format"], "carousel")
        self.assertLessEqual(len(package["slides"]), 6)
        self.assertGreaterEqual(len(package["slides"]), 1)
        self.assertNotIn("transcript", package)
        self.assertNotIn("voiceover", package["slides"][0])
        self.assertTrue(package["slides"][0]["image_queries"])

    def test_build_pack_title_uses_first_section(self):
        title = build_pack_title("Port to warehouse coordination keeps breaking across teams.\n\nVisibility gets delayed.")
        self.assertIn("Port To Warehouse Coordination", title)

    def test_ai_boundaries_preserve_every_original_paragraph(self):
        paragraphs = [
            "The shipment leaves the port.",
            "Documents move through email.",
            "The warehouse team receives the cargo.",
        ]
        scenes = _normalize_boundaries(paragraphs, [
            {
                "start_paragraph": 1,
                "end_paragraph": 2,
                "background_subject": "Port cargo operation",
                "pexels_query": "container ship cargo port",
                "change_reason": "The setting starts at the port.",
            },
            {
                "start_paragraph": 3,
                "end_paragraph": 3,
                "background_subject": "Warehouse receiving operation",
                "pexels_query": "warehouse worker receiving freight",
                "change_reason": "The setting moves to a warehouse.",
            },
        ])
        rebuilt = "\n\n".join(scene["body"] for scene in scenes)
        self.assertEqual(rebuilt, "\n\n".join(paragraphs))
        self.assertEqual([scene["paragraph_start"] for scene in scenes], [1, 3])
        self.assertEqual(scenes[1]["pexels_query"], "warehouse worker receiving freight")

    def test_scene_planner_falls_back_without_losing_script(self):
        script = "Cargo leaves the port.\n\nDocuments reach the warehouse."
        with patch(
            "services.asset_scene_planner._request_ai_boundaries",
            side_effect=RuntimeError("planner unavailable"),
        ):
            plan = plan_script_scenes(script)
        self.assertEqual(plan["source"], "fallback")
        self.assertEqual(
            "\n\n".join(scene["body"] for scene in plan["scenes"]),
            script,
        )

    def test_target_length_produces_nine_scene_target(self):
        paragraphs = [f"Spoken paragraph number {index}." for index in range(1, 16)]
        script = "Target length: 90–120 seconds\n\n" + "\n\n".join(paragraphs)
        metrics = _script_metrics(script, paragraphs)
        self.assertEqual(metrics["target_duration_seconds"], 105)
        self.assertEqual(metrics["duration_source"], "script")
        self.assertEqual(metrics["target_scene_count"], 9)

    def test_two_scene_ai_result_is_rejected_for_nine_scene_target(self):
        plan = SceneBoundaryPlan.model_validate({"scenes": [
            {
                "start_paragraph": 1,
                "end_paragraph": 5,
                "background_subject": "Freight office",
                "pexels_query": "freight office documents",
                "change_reason": "Opening problem",
            },
            {
                "start_paragraph": 6,
                "end_paragraph": 15,
                "background_subject": "Cargo warehouse",
                "pexels_query": "cargo warehouse workers",
                "change_reason": "Operational solution",
            },
        ]})
        self.assertIn("exactly 9 scenes", _boundary_error(plan, 15, 9))


if __name__ == "__main__":
    unittest.main()
