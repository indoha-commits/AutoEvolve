import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from g2_runtime.coverr import CoverrProvider
from g2_runtime.media_acquisition import _download


class Response(io.BytesIO):
    def __init__(self, payload: bytes, url: str = "https://api.coverr.co/videos"):
        super().__init__(payload)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def geturl(self):
        return self.url


class CoverrProviderTests(unittest.TestCase):
    def test_search_returns_deferred_video_candidates(self):
        payload = json.dumps([{
            "id": 42,
            "base_filename": "Cargo-Port",
            "description": "Cargo containers moving through a port",
            "full_image_path": "https://storage.googleapis.com/coverr-public/poster/cargo.jpg",
            "is_vertical": True,
        }]).encode()
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response(payload)

        result = CoverrProvider(api_key="test-key", opener=opener).search("cargo port")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].provider, "coverr")
        self.assertEqual(result[0].media_type, "video")
        self.assertEqual(result[0].width, 1080)
        self.assertEqual(result[0].download_url, "https://api.coverr.co/storage/videos/Cargo-Port")
        self.assertIn("query=cargo+port", requests[0].full_url)
        self.assertEqual(requests[0].get_header("Api_key"), "test-key")

    def test_image_search_is_skipped_without_using_the_api(self):
        opener = Mock()
        self.assertEqual(CoverrProvider(api_key="test-key", opener=opener).search("cargo", media_type="image"), [])
        opener.assert_not_called()

    def test_download_resolves_signed_url_after_selection(self):
        candidate = CoverrProvider(
            api_key="test-key",
            opener=lambda *_args, **_kwargs: Response(json.dumps([{
                "id": 42,
                "base_filename": "Cargo-Port",
                "description": "cargo port",
            }]).encode()),
        ).search("cargo")[0]
        signed_url = "https://storage.googleapis.com/coverr-public/videos/Cargo-Port.mp4?signature=test"
        responses = [
            Response(json.dumps(signed_url).encode(), candidate.download_url),
            Response(b"video-bytes", signed_url),
        ]
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"COVERR_API_KEY": "test-key"}), \
                patch("g2_runtime.media_acquisition.urllib.request.urlopen", side_effect=responses) as urlopen:
            destination = Path(temp) / "clip.mp4"
            _download(candidate, destination)
            self.assertEqual(destination.read_bytes(), b"video-bytes")
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(urlopen.call_args_list[0].args[0].get_header("Api_key"), "test-key")


if __name__ == "__main__":
    unittest.main()
