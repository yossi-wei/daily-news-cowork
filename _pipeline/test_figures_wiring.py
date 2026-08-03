#!/usr/bin/env python3
"""
Step 2 wiring: config gates and briefing fragments for optional figures.

The property these tests exist to protect is that the feature is *inert* when off --
not merely harmless, but absent. A topic with figures disabled must receive an agent
prompt byte-identical to one generated before the feature existed, because any mention
of figures (even a prohibition) measurably raises the chance a model produces one.
"""
import json
import unittest
from os.path import abspath, dirname, join
import sys

HERE = dirname(abspath(__file__))
sys.path.insert(0, HERE)

import build_context as bc  # noqa: E402

BRIEFING = join(HERE, "briefing")


def cfg(enabled=True, **kw):
    d = {"figures": {"enabled": enabled}}
    d.update(kw)
    return d


class TestFigureModeGates(unittest.TestCase):
    def test_disabled_globally_overrides_topic(self):
        self.assertEqual(
            bc._figures_mode(cfg(False), {"key": "Space", "figures": "chart"}), "none")

    def test_enabled_plus_topic_chart(self):
        self.assertEqual(
            bc._figures_mode(cfg(True), {"key": "Space", "figures": "chart"}), "chart")

    def test_enabled_but_topic_none(self):
        self.assertEqual(
            bc._figures_mode(cfg(True), {"key": "Middle East", "figures": "none"}), "none")

    def test_topic_without_figures_key(self):
        self.assertEqual(bc._figures_mode(cfg(True), {"key": "Quantum"}), "none")

    def test_config_without_figures_key_at_all(self):
        """The upgrade path: a config written before this feature existed."""
        self.assertEqual(bc._figures_mode({}, {"key": "Space", "figures": "chart"}), "none")

    def test_wildcard_never_gets_figures(self):
        self.assertEqual(
            bc._figures_mode(cfg(True), {"key": "Wild Card", "figures": "chart",
                                         "_wildcard": True}), "none")

    def test_unknown_mode_falls_back_to_none(self):
        """A typo in config should cost a figure, not the run."""
        for bad in ("charts", "image", "true", "", None, 3):
            self.assertEqual(
                bc._figures_mode(cfg(True), {"key": "X", "figures": bad}), "none")

    def test_figures_not_a_dict(self):
        self.assertEqual(
            bc._figures_mode({"figures": None}, {"key": "X", "figures": "chart"}), "none")


class TestBriefingFragments(unittest.TestCase):
    def test_figures_none_is_literally_empty(self):
        """Not a 'do not include figures' sentence. Naming figures in order to forbid
        them is what makes a model start considering them, and a non-empty fragment
        would also break prompt byte-identity when the feature is off."""
        with open(join(BRIEFING, "figures_none.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_main_has_placeholder(self):
        with open(join(BRIEFING, "main.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("__FIGURES_BLOCK__", text)

    def test_placeholder_occupies_a_blank_line(self):
        """Empty substitution must reproduce the original blank line exactly -- that is
        what makes a figures-off prompt byte-identical rather than merely equivalent."""
        with open(join(BRIEFING, "main.md"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        i = lines.index("__FIGURES_BLOCK__")
        self.assertEqual(lines[i - 1], "__ENRICHMENT_BLOCK__")
        self.assertTrue(lines[i + 1].startswith("## "),
                        "placeholder should sit where the blank line before a heading was")

    def test_chart_fragment_states_the_four_tests_and_scarcity(self):
        with open(join(BRIEFING, "figures_chart.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Most days you should attach none", text)
        for token in ("series_source", "why_it_qualifies", "chart_type",
                      "series_unit", "estimated"):
            self.assertIn(token, text)

    def test_chart_fragment_schema_is_valid_json(self):
        """The JSON block the agent is told to copy must actually parse."""
        with open(join(BRIEFING, "figures_chart.md"), encoding="utf-8") as fh:
            text = fh.read()
        start = text.index('"figure": {')
        block = text[start:text.index("```", start)].rstrip()
        json.loads("{" + block + "}")


class TestConfigShape(unittest.TestCase):
    def setUp(self):
        with open(join(HERE, "config.example.json"), encoding="utf-8") as fh:
            self.example = json.load(fh)

    def test_example_ships_figures_block_disabled(self):
        self.assertIn("figures", self.example)
        self.assertIs(self.example["figures"]["enabled"], False,
                      "the shipped example must default to off")

    def test_example_topics_all_none(self):
        for t in self.example["topics"]:
            self.assertEqual(t.get("figures"), "none",
                             f"{t['key']} should ship with figures off")

    def test_chart_bounds_present(self):
        chart = self.example["figures"]["chart"]
        for key in ("min_points", "max_points", "max_label_chars", "aspect"):
            self.assertIn(key, chart)
        self.assertLess(chart["min_points"], chart["max_points"])

    def test_min_points_rules_out_a_two_bar_chart(self):
        """A chart of two or three numbers is a sentence."""
        self.assertGreaterEqual(self.example["figures"]["chart"]["min_points"], 4)

    def test_renderer_defaults_match_shipped_config(self):
        """render_chart.py's fallbacks and the shipped config must not drift apart."""
        import render_chart as rc
        chart = self.example["figures"]["chart"]
        for key in ("min_points", "max_points", "max_label_chars", "aspect"):
            self.assertEqual(rc.DEFAULTS[key], chart[key],
                             f"{key} differs between render_chart.DEFAULTS and config.example")


if __name__ == "__main__":
    unittest.main(verbosity=2)
