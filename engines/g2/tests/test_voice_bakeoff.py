import unittest

from g2_runtime.voice_bakeoff import parse_edge_voice_list


class VoiceBakeoffTests(unittest.TestCase):
    def test_edge_voice_parser_returns_only_requested_language(self):
        value = """Name Gender
en-US-AndrewNeural Male
fr-FR-HenriNeural Male
en-GB-SoniaNeural Female
en-US-AndrewNeural Male
"""
        self.assertEqual(
            parse_edge_voice_list(value),
            ["en-GB-SoniaNeural", "en-US-AndrewNeural"],
        )


if __name__ == "__main__":
    unittest.main()
