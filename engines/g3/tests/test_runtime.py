import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from g3_runtime.buffer import BufferClient
from g3_runtime.errors import G3Error
from g3_runtime.handoff import SCHEMA, load_handoff
from g3_runtime.ledger import Ledger
from g3_runtime.r2 import verify_public_media
from g3_runtime.service import build_input, idempotency_key, submit_handoff


class FakeClient:
    def __init__(self): self.created = []
    def create_draft(self, post_input):
        self.created.append(post_input)
        return {"id": f"draft-{len(self.created)}", "status": "draft", "text": post_input["text"]}


class FakeUploader:
    def __init__(self): self.uploads = []
    def upload(self, campaign_id, media):
        self.uploads.append((campaign_id, media.path.name))
        return f"https://media.example/{campaign_id}/{media.path.name}"


class FakeResponse:
    def __init__(self, body=b"\x00\x00\x00\x18ftypmp42", content_type="video/mp4", status=206,
                 url="https://media.example/video.mp4", headers=None):
        self.body = body
        self.status = status
        self.url = url
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self, _size=-1): return self.body
    def getcode(self): return self.status
    def geturl(self): return self.url


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.media = self.root / "slide.png"
        self.media.write_bytes(b"png-placeholder")
        self.sha = hashlib.sha256(self.media.read_bytes()).hexdigest()

    def tearDown(self): self.tmp.cleanup()

    def write(self, value):
        path = self.root / "handoff.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def base(self):
        return {
            "schema": SCHEMA, "campaign_id": "camp_1", "source_package_sha256": "a" * 64,
            "publish_allowed": False,
            "drafts": [{"platform": "instagram", "channel_id": "ig1", "content": "Caption",
                        "media": [{"path": "slide.png", "sha256": self.sha}]}],
        }

    def test_ready_handoff_passes(self):
        self.assertEqual(load_handoff(self.write(self.base())).campaign_id, "camp_1")

    def test_publish_allowed_is_denied(self):
        value = self.base(); value["publish_allowed"] = True
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_schedule_field_is_denied(self):
        value = self.base(); value["scheduled_at"] = "tomorrow"
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_asset_escape_is_denied(self):
        value = self.base(); value["drafts"][0]["media"][0]["path"] = "../outside.png"
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_hash_mismatch_is_denied(self):
        value = self.base(); value["drafts"][0]["media"][0]["sha256"] = "bad"
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_instagram_requires_media(self):
        value = self.base(); value["drafts"][0]["media"] = []
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_obsolete_postiz_integration_id_is_denied(self):
        value = self.base(); value["drafts"][0]["integration_id"] = "old"
        with self.assertRaises(G3Error): load_handoff(self.write(value))

    def test_x_thread_payload_contains_root_and_reply(self):
        value = self.base()
        value["drafts"] = [{"platform": "x", "channel_id": "x1", "thread": [
            {"content": "one", "media": []}, {"content": "two", "media": []}]}]
        entry = load_handoff(self.write(value)).entries[0]
        payload = build_input(entry, "x1", [[], []])
        self.assertEqual(payload["text"], "one")
        self.assertEqual([part["text"] for part in payload["metadata"]["twitter"]["thread"]], ["one", "two"])
        self.assertIs(payload["saveToDraft"], True)

    def test_instagram_payload_is_ordered_and_draft_only(self):
        entry = load_handoff(self.write(self.base())).entries[0]
        payload = build_input(entry, "ig1", [["https://media.example/slide.png"]])
        self.assertEqual(payload["assets"][0]["image"]["url"], "https://media.example/slide.png")
        self.assertEqual(payload["metadata"]["instagram"]["type"], "post")
        self.assertNotIn("dueAt", payload)
        self.assertTrue(payload["saveToDraft"])

    def test_submit_confirms_and_records_draft(self):
        handoff = load_handoff(self.write(self.base()))
        client, uploader = FakeClient(), FakeUploader()
        ledger = Ledger(self.root / "ledger.db")
        report = submit_handoff(client, uploader, ledger, handoff)
        self.assertEqual(report["results"][0]["status"], "draft_confirmed")
        self.assertTrue(client.created[0]["saveToDraft"])
        self.assertEqual(uploader.uploads, [("camp_1", "slide.png")])

    def test_idempotency_skips_duplicate(self):
        handoff = load_handoff(self.write(self.base()))
        client, uploader = FakeClient(), FakeUploader()
        ledger = Ledger(self.root / "ledger.db")
        submit_handoff(client, uploader, ledger, handoff)
        report = submit_handoff(client, uploader, ledger, handoff)
        self.assertEqual(len(client.created), 1)
        self.assertEqual(report["results"][0]["status"], "duplicate_skipped")

    def test_key_is_stable(self):
        handoff = load_handoff(self.write(self.base())); entry = handoff.entries[0]
        self.assertEqual(idempotency_key(handoff, entry, "ig1"), idempotency_key(handoff, entry, "ig1"))

    def test_buffer_boundary_rejects_non_draft(self):
        client = BufferClient("test")
        with self.assertRaises(G3Error):
            client.create_draft({"saveToDraft": False, "mode": "shareNow"})

    @patch("g3_runtime.r2.urlopen", return_value=FakeResponse())
    def test_public_mp4_probe_passes(self, mocked):
        video = self.root / "video.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        verify_public_media(
            "https://media.example/video.mp4",
            type("M", (), {"path": video})(),
            "video/mp4",
            attempts=1,
        )
        self.assertEqual(mocked.call_count, 1)

    @patch("g3_runtime.r2.urlopen", return_value=FakeResponse(content_type="text/html", body=b"login"))
    def test_public_mp4_probe_rejects_html(self, _mocked):
        video = self.root / "video.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        with self.assertRaisesRegex(G3Error, "Content-Type text/html"):
            verify_public_media(
                "https://media.example/video.mp4",
                type("M", (), {"path": video})(),
                "video/mp4",
                attempts=1,
            )

    @patch("g3_runtime.r2.urlopen", return_value=FakeResponse(headers={"cf-mitigated": "challenge"}))
    def test_public_mp4_probe_rejects_cloudflare_challenge(self, _mocked):
        video = self.root / "video.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        with self.assertRaisesRegex(G3Error, "Cloudflare challenge"):
            verify_public_media(
                "https://media.example/video.mp4",
                type("M", (), {"path": video})(),
                "video/mp4",
                attempts=1,
            )


if __name__ == "__main__": unittest.main()
