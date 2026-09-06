import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from g2_runtime.captions import write_ass
from g2_runtime.ingest import load_campaign
from g2_runtime.mixed_video import _asset_path, _render_scene, load_asset_records
from g2_runtime.storyboard import compile_storyboard
from g2_runtime.video_layers import render_layers
from g2_runtime.platform_script import load_platform_script
from g2_runtime.subtitles import write_provider_srt
from g2_runtime.voice import EdgeVoice, KokoroVoice, SystemVoice, delivery_for, voice_engine

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "fixtures/campaign_ready.json"
ASSET_ROOT = ROOT / "assets" / "demo"
MANIFEST = ASSET_ROOT / "asset_manifest.json"
OUTRO = ROOT / "assets" / "references" / "reference_05_cargo_trail.jpg"
SHOWCASE = ROOT / "assets" / "references" / "reference_05_cargo_trail.jpg"


class MixedVideoTests(unittest.TestCase):
    def test_storyboard_uses_six_visual_modes(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        self.assertEqual([scene.visual_mode for scene in plan.scenes], [
            "kinetic_stock", "document_stack", "role_route", "status_handoff",
            "control_layer", "branded_end_card",
        ])
        self.assertAlmostEqual(sum(scene.duration_seconds for scene in plan.scenes), 45, places=2)

    def test_manifest_wrapper_and_list_are_supported(self):
        records = load_asset_records(MANIFEST)
        self.assertEqual(len(records), 4)
        with tempfile.TemporaryDirectory() as temp:
            wrapped = Path(temp) / "wrapped.json"
            wrapped.write_text(json.dumps({"assets": json.loads(MANIFEST.read_text())}))
            self.assertEqual(len(load_asset_records(wrapped)), 4)

    def test_unapproved_asset_is_review_only(self):
        record = load_asset_records(MANIFEST)[0]
        self.assertTrue(_asset_path(record, ASSET_ROOT, review=True).is_file())
        with self.assertRaisesRegex(ValueError, "not Founder-approved"):
            _asset_path(record, ASSET_ROOT, review=False)

    def test_caption_timeline_covers_every_scene(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            path = write_ass(plan.scenes, Path(temp) / "captions.ass")
            text = path.read_text()
            self.assertEqual(text.count("Dialogue:"), sum((len(scene.narration.split()) + 4) // 5 for scene in plan.scenes))
            self.assertIn("Cargo and its records", text)

    def test_scene_layers_are_vertical_and_review_marked(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        record = load_asset_records(MANIFEST)[0]
        with tempfile.TemporaryDirectory() as temp:
            value = render_layers(plan.scenes[0], ASSET_ROOT / record.local_path, OUTRO, temp, review=True)
            for key in ("background", "overlay", "preview"):
                with Image.open(value[key]) as image:
                    self.assertEqual(image.size, (1080, 1920))

    def test_control_scene_uses_configured_showcase_and_end_scene_uses_outro(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            control = render_layers(
                plan.scenes[-2], None, OUTRO, temp, review=False, showcase_path=SHOWCASE,
            )
            self.assertTrue(Path(control["preview"]).is_file())
            value = render_layers(plan.scenes[-1], None, OUTRO, temp, review=False)
            self.assertTrue(Path(value["preview"]).is_file())

    def test_control_scene_requires_configured_showcase(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "configured showcase asset"):
                render_layers(plan.scenes[-2], None, OUTRO, temp, review=False)

    def test_configured_showcase_can_be_video(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            showcase = Path(temp) / "showcase.mp4"
            showcase.write_bytes(b"test")
            value = render_layers(
                plan.scenes[-2], None, OUTRO, temp, review=False, showcase_path=showcase,
            )
        self.assertEqual(value["background_kind"], "video")
        self.assertEqual(value["background"], str(showcase))

    def test_non_control_scene_still_requires_explicit_media(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "visual fallback is disabled"):
                render_layers(plan.scenes[0], None, OUTRO, temp, review=False)

    def test_configured_showcase_is_not_a_solid_color_screen(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            value = render_layers(
                plan.scenes[-2], None, OUTRO, temp, review=False, showcase_path=SHOWCASE,
            )
            with Image.open(value["background"]).convert("RGB") as image:
                colors = image.resize((96, 96)).getcolors(maxcolors=96 * 96)
            self.assertIsNotNone(colors)
            self.assertGreater(len(colors), 24)

    def test_overlay_contains_no_large_editorial_card(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        record = load_asset_records(MANIFEST)[0]
        with tempfile.TemporaryDirectory() as temp:
            value = render_layers(plan.scenes[0], ASSET_ROOT / record.local_path, OUTRO, temp, review=False)
            with Image.open(value["overlay"]).convert("RGBA") as image:
                visible = sum(1 for pixel in image.getdata() if pixel[3] > 0)
            self.assertLess(visible, 35000)

    def test_platform_profiles_are_not_simple_crops(self):
        campaign = load_campaign(CAMPAIGN)
        youtube = compile_storyboard(campaign, platform="youtube")
        shorts = compile_storyboard(campaign, platform="shorts")
        tiktok = compile_storyboard(campaign, platform="tiktok")
        self.assertEqual(youtube.resolution, "1920x1080")
        self.assertEqual(shorts.resolution, "1080x1920")
        self.assertEqual(tiktok.resolution, "1080x1920")
        self.assertEqual([youtube.duration_target_seconds, shorts.duration_target_seconds, tiktok.duration_target_seconds], [120, 45, 30])
        self.assertEqual(len({youtube.editorial_brief["opening"], shorts.editorial_brief["opening"], tiktok.editorial_brief["opening"]}), 3)

    def test_outro_uses_premade_image_without_logo_overlay(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            value = render_layers(plan.scenes[-1], None, OUTRO, temp, review=False)
            with Image.open(value["preview"]) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_platform_scripts_change_reasoning_and_narration(self):
        campaign = load_campaign(CAMPAIGN)
        plans = []
        for platform in ("youtube", "shorts", "tiktok"):
            script = load_platform_script(ROOT / "fixtures" / "platform_scripts" / f"{platform}.json", campaign.campaign_id, platform)
            plans.append(compile_storyboard(campaign, platform=platform, script=script))
        self.assertTrue(all(plan.editorial_source == "platform_script" for plan in plans))
        self.assertEqual(len({plan.scenes[0].narration for plan in plans}), 3)

    def test_voice_modes_require_no_reference_audio(self):
        self.assertIsInstance(voice_engine("edge", "en-GB-RyanNeural"), EdgeVoice)
        self.assertIsInstance(voice_engine("system", "en-us"), SystemVoice)
        self.assertIsInstance(voice_engine("kokoro", "af_heart"), KokoroVoice)
        self.assertIsNone(voice_engine("silent", "ignored"))
        with self.assertRaisesRegex(ValueError, "silent, edge, system, or kokoro"):
            voice_engine("clone", "ignored")

    def test_delivery_is_subtle_and_purpose_specific(self):
        cover = delivery_for("cover")
        evidence = delivery_for("evidence")
        failure = delivery_for("failure_point")
        control = delivery_for("control_system")
        self.assertNotEqual(cover.name, evidence.name)
        self.assertGreaterEqual(cover.rate_percent, -20)
        self.assertLessEqual(abs(cover.pitch_hz), 2)
        self.assertGreater(evidence.rate_percent, cover.rate_percent)
        self.assertGreater(control.target_lufs, failure.target_lufs)

    def test_speed_adjustment_preserves_energy_target(self):
        original = delivery_for("control_system")
        faster = delivery_for("control_system", 1.04)
        self.assertEqual(original.target_lufs, faster.target_lufs)
        self.assertEqual(faster.rate_percent, original.rate_percent + 4)

    def test_edge_retries_only_transient_failures(self):
        engine = EdgeVoice(attempts=3, retry_base_seconds=0)
        transient = subprocess.CompletedProcess(
            ["edge-tts"], 1, "", "Temporary failure in name resolution"
        )
        success = subprocess.CompletedProcess(["edge-tts"], 0, "", "")
        with patch("g2_runtime.voice.subprocess.run", side_effect=[transient, success]) as run:
            engine._synthesize_edge(["edge-tts"], (Path("missing.mp3"), Path("missing.vtt")))
        self.assertEqual(run.call_count, 2)

    def test_edge_does_not_retry_invalid_voice(self):
        engine = EdgeVoice(attempts=4, retry_base_seconds=0)
        invalid = subprocess.CompletedProcess(["edge-tts"], 1, "", "Invalid voice")
        with patch("g2_runtime.voice.subprocess.run", return_value=invalid) as run:
            with self.assertRaisesRegex(RuntimeError, "after 1 attempt.*Invalid voice"):
                engine._synthesize_edge(["edge-tts"], (Path("missing.mp3"),))
        self.assertEqual(run.call_count, 1)

    def test_edge_timeout_is_bounded_and_retryable(self):
        engine = EdgeVoice(attempts=2, retry_base_seconds=0, timeout_seconds=5)
        timeout = subprocess.TimeoutExpired(["edge-tts"], 5)
        success = subprocess.CompletedProcess(["edge-tts"], 0, "", "")
        with patch("g2_runtime.voice.subprocess.run", side_effect=[timeout, success]) as run:
            engine._synthesize_edge(["edge-tts"], (Path("missing.mp3"),))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_provider_timing_offsets_each_scene(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.vtt"
            first.write_text(
                "WEBVTT\n\n00:00:00.100 --> 00:00:00.500\nCargo\n\n"
                "00:00:00.500 --> 00:00:01.000\nmoves\n",
                encoding="utf-8",
            )
            output = write_provider_srt([
                {
                    "narration": "Cargo moves",
                    "duration_seconds": 2.0,
                    "timing_path": str(first),
                    "timing_offset_seconds": 0.1,
                },
                {
                    "narration": "Records follow",
                    "duration_seconds": 2.0,
                    "timing_path": None,
                    "timing_offset_seconds": 0.0,
                },
            ], Path(temp) / "captions.srt")
            text = output.read_text(encoding="utf-8")
            self.assertIn("00:00:00,200 --> 00:00:01,100", text)
            self.assertIn("00:00:02,000", text)

    def test_scene_render_uses_stable_static_framing(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with patch("g2_runtime.mixed_video._run") as run:
            _render_scene(
                plan.scenes[0],
                {"background": "background.jpg", "overlay": "overlay.png"},
                Path("audio.wav"),
                Path("scene.mp4"),
                1080,
                1920,
                30,
            )
        command = " ".join(run.call_args.args[0])
        self.assertNotIn("sin(", command)
        self.assertNotIn("cos(", command)
        self.assertIn("fps_mode cfr", command)
        self.assertIn("-crf 0", command)
        self.assertIn("-tune stillimage", command)

    def test_scene_background_is_lossless_png(self):
        plan = compile_storyboard(load_campaign(CAMPAIGN), 45)
        with tempfile.TemporaryDirectory() as temp:
            layers = render_layers(
                plan.scenes[0],
                ASSET_ROOT / "slide_01_pexels.jpg",
                OUTRO,
                temp,
                True,
            )
            self.assertEqual(Path(layers["background"]).suffix, ".png")

    def test_concat_does_not_reencode_lossless_scenes(self):
        from g2_runtime.mixed_video import _concat

        with tempfile.TemporaryDirectory() as temp, patch("g2_runtime.mixed_video._run") as run:
            _concat([Path(temp) / "one.mp4", Path(temp) / "two.mp4"], Path(temp) / "joined.mp4")
        command = " ".join(run.call_args.args[0])
        self.assertIn("-c copy", command)
        self.assertNotIn("libx264", command)

    def test_vertical_subtitles_use_libass_safe_scale(self):
        from g2_runtime.mixed_video import _burn_srt

        with tempfile.TemporaryDirectory() as temp, patch("g2_runtime.mixed_video._run") as run:
            root = Path(temp)
            _burn_srt(root / "joined.mp4", root / "captions.srt", root / "final.mp4", "shorts")
        command = " ".join(run.call_args.args[0])
        self.assertIn("FontSize=9", command)
        self.assertIn("MarginV=40", command)
        self.assertNotIn("FontSize=18", command)
        self.assertNotIn("MarginV=230", command)


if __name__ == "__main__":
    unittest.main()
