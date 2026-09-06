from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from g2_runtime.models import AssetCandidate
from g2_runtime.scene_images import search_scene_images


def candidate(identifier: str, query: str) -> AssetCandidate:
    return AssetCandidate(
        candidate_id=identifier,
        provider="pexels",
        source_type="stock",
        query=query,
        description=query,
        source_url="https://www.pexels.com/photo/example",
        download_url="https://images.pexels.com/photos/example.jpeg",
        license="Pexels license",
        width=1600,
        height=2400,
    )


class FakePexels:
    def __init__(self, timeout: int):
        self.timeout = timeout

    def search(self, query: str, limit: int, media_type: str):
        return [candidate(f"pexels:{query}", query)]


class SceneImageTests(unittest.TestCase):
    def test_ai_queries_map_directly_to_distinct_pexels_images(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = Path(temp) / "plan.json"
            plan.write_text(json.dumps({"scenes": [
                {"number": 1, "pexels_query": "container cargo port"},
                {"number": 2, "pexels_query": "warehouse worker documents"},
            ]}), encoding="utf-8")
            with patch("g2_runtime.scene_images.PexelsProvider", FakePexels):
                result = search_scene_images(plan, deadline=2, limit=2)
        self.assertEqual([scene["query"] for scene in result["scenes"]], [
            "container cargo port",
            "warehouse worker documents",
        ])
        self.assertTrue(all(scene["selected"]["media_type"] == "image" for scene in result["scenes"]))
        self.assertEqual(len({scene["selected"]["candidate_id"] for scene in result["scenes"]}), 2)


if __name__ == "__main__":
    unittest.main()
