import unittest

from services.caption_variants import build_short_caption_variants, _buyer_label


class MarketingBuyerFallbackTests(unittest.TestCase):
    def test_buyer_label_falls_back_from_ai_selected(self):
        label = _buyer_label("ai_selected", "Port to warehouse coordination", "Cargo updates are fragmented across teams.", {})
        self.assertEqual(label, "freight ops teams")

    def test_short_caption_variants_do_not_echo_ai_selected(self):
        variants = build_short_caption_variants(
            topic="EXAMPLE COMPANY - FACE VIDEO #1",
            buyer="ai_selected",
            transcript="Cargo updates are fragmented across logistics handoffs.",
            platform_copy={"instagram_caption": "See how Example Company fits your operation."},
        )
        joined = "\n".join(variants["instagram"])
        self.assertNotIn("ai selected", joined.lower())
        self.assertIn("freight ops teams", joined.lower())


if __name__ == "__main__":
    unittest.main()
