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


import build_digest as bd  # noqa: E402

SRC = "https://example.gov/reports/2026/spend"


def chart(**over):
    f = {"kind": "chart", "chart_type": "bar",
         "series": [{"label": f"Q{i}", "value": v}
                    for i, v in enumerate([10, 20, 15, 30], 1)],
         "series_unit": "billion USD", "series_source": SRC,
         "alt": "Spending rose across four quarters.",
         "caption": "Doubled in a year.",
         "why_it_qualifies": "test 2"}
    f.update(over)
    return f


def story(sid="s1", **over):
    s = {"id": sid, "category": "AI & Tech", "headline": f"Headline {sid}",
         "body": ["p"], "urls": [SRC], "figure": chart()}
    s.update(over)
    return s


ON = {"figures": {"enabled": True, "max_per_digest": 2, "chart": {}}}


class TestFigurePolicy(unittest.TestCase):
    def test_disabled_strips_figure_without_rendering(self):
        ss = [story()]
        n, rej = bd.apply_figure_policy(ss, {"figures": {"enabled": False}}, {})
        self.assertEqual((n, rej), (0, []))
        self.assertNotIn("figure", ss[0])
        self.assertNotIn("_figure_html", ss[0])

    def test_config_without_figures_key(self):
        ss = [story()]
        self.assertEqual(bd.apply_figure_policy(ss, {}, {})[0], 0)
        self.assertNotIn("figure", ss[0])

    def test_valid_figure_renders(self):
        ss = [story()]
        n, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertEqual((n, rej), (1, []))
        self.assertIn("fig-bars", ss[0]["_figure_html"])

    def test_raw_figure_object_never_survives(self):
        """Whatever happens, the unrendered dict must not reach the page."""
        ss = [story("s1"), story("s2", figure=chart(series_unit="")),
              story("s3", urls=["https://elsewhere.com/a/b"])]
        bd.apply_figure_policy(ss, ON, {})
        for s in ss:
            self.assertNotIn("figure", s)

    def test_cap_enforced(self):
        ss = [story(f"s{i}") for i in range(5)]
        n, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertEqual(n, 2)
        self.assertEqual(sum("_figure_html" in s for s in ss), 2)
        self.assertEqual(len(rej), 3)

    def test_cap_of_zero_renders_nothing(self):
        ss = [story()]
        n, _ = bd.apply_figure_policy(ss, {"figures": {"enabled": True,
                                                       "max_per_digest": 0}}, {})
        self.assertEqual(n, 0)

    def test_invalid_figures_report_their_real_reason_not_the_cap(self):
        """Validation runs before the cap. If it did not, a flat series in a full
        digest would be logged as 'cap reached' and the rejection log -- the whole
        evidence base for tightening the gate -- would say nothing useful."""
        ss = [story("s1"), story("s2"),
              story("s3", figure=chart(series=[{"label": f"p{i}", "value": 7}
                                               for i in range(5)])),
              story("s4", urls=["https://elsewhere.com/a/b"])]
        n, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertEqual(n, 2)
        reasons = {h: r for h, r in rej}
        self.assertIn("flat series", reasons["Headline s3"])
        self.assertIn("not among the story's cited urls", reasons["Headline s4"])
        for r in reasons.values():
            self.assertNotIn("cap", r)

    def test_overflow_reason_says_it_was_valid(self):
        ss = [story(f"s{i}") for i in range(3)]
        _, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertIn("valid, but the per-digest cap", rej[0][1])

    def test_oyr_and_wildcard_refused(self):
        ss = [story("oyr1", oyr=True), story("wc1", wildcard=True)]
        n, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertEqual(n, 0)
        self.assertEqual(len(rej), 2)
        for _, reason in rej:
            self.assertIn("On Your Radar", reason)

    def test_story_without_urls_cannot_pass_traceability(self):
        ss = [story(urls=[])]
        n, rej = bd.apply_figure_policy(ss, ON, {})
        self.assertEqual(n, 0)
        self.assertIn("cited urls", rej[0][1])

    def test_uses_category_color(self):
        """main() populates CATEGORY_SLUG from config.topics before this runs, so the
        lookup resolves a display name to its configured slug."""
        saved = dict(bd.CATEGORY_SLUG)
        try:
            bd.CATEGORY_SLUG["AI & Tech"] = "ai"
            ss = [story()]
            bd.apply_figure_policy(ss, ON, {"ai": {"border": "#abcdef"}})
            self.assertIn("#abcdef", ss[0]["_figure_html"])
        finally:
            bd.CATEGORY_SLUG.clear()
            bd.CATEGORY_SLUG.update(saved)

    def test_unknown_category_falls_back_to_default_color(self):
        ss = [story(category="Not In Config")]
        bd.apply_figure_policy(ss, ON, {})
        self.assertIn("background:#", ss[0]["_figure_html"])

    def test_missing_renderer_degrades_to_rejections(self):
        real = bd.render_chart
        try:
            bd.render_chart = None
            ss = [story()]
            n, rej = bd.apply_figure_policy(ss, ON, {})
            self.assertEqual(n, 0)
            self.assertIn("unavailable", rej[0][1])
            self.assertNotIn("figure", ss[0])
        finally:
            bd.render_chart = real

    def test_malformed_figure_is_rejected_not_raised(self):
        for bad in ("nope", 42, {"kind": "chart"}, {"kind": "image"}):
            s = {"id": "x", "category": "AI & Tech", "headline": "h",
                 "urls": [SRC], "figure": bad}
            n, rej = bd.apply_figure_policy([s], ON, {})
            self.assertEqual(n, 0)
            self.assertTrue(rej, f"{bad!r} should have produced a logged reason")

    def test_absent_figure_is_not_a_rejection(self):
        """A story that simply offered no figure is the normal case, not a refusal --
        logging it would bury the real rejections in noise."""
        for empty in (None, [], {}, ""):
            s = {"id": "x", "category": "AI & Tech", "headline": "h",
                 "urls": [SRC], "figure": empty}
            n, rej = bd.apply_figure_policy([s], ON, {})
            self.assertEqual((n, rej), (0, []))


class TestCardAndCssIntegration(unittest.TestCase):
    def test_rendered_figure_appears_in_card(self):
        s = story()
        bd.apply_figure_policy([s], ON, {})
        html = bd.render_card(s, 1, "2026-08-03")
        self.assertIn("fig-bars", html)

    def test_card_without_figure_is_unchanged(self):
        s = story(figure=None)
        bd.apply_figure_policy([s], ON, {})
        self.assertNotIn("story-figure", bd.render_card(s, 1, "2026-08-03"))

    def test_css_block_round_trips(self):
        with open(join(HERE, "template.html"), encoding="utf-8") as fh:
            tpl = fh.read()
        self.assertIn(".fb-track", bd.extract_figure_css(tpl))
        self.assertNotIn(".fb-track", bd.strip_figure_css(tpl))


class TestCarryFields(unittest.TestCase):
    def test_figure_and_urls_are_carried(self):
        """A field absent from CARRY_FIELDS is dropped between the agent return and
        the stories JSON with no error -- the silent-failure point for this feature."""
        import assemble_stories as asm
        self.assertIn("figure", asm.CARRY_FIELDS)
        self.assertIn("urls", asm.CARRY_FIELDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
