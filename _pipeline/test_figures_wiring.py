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


import validate_digest as vd  # noqa: E402

BARS = ('<div class="fig-bars" role="img" aria-label="a chart">'
        '<div class="fb-row"><span class="fb-track"></span></div></div>')
LINE = ('<svg class="fig-line" role="img" aria-labelledby="t"><title id="t">x</title>'
        '<polyline points="0,0 1,1"/></svg>')
CSS = "/* FIGURE-CSS-START */ .fb-track { background: #f1f5f9; } /* FIGURE-CSS-END */"


def page(*figs):
    body = "".join(f'<figure class="story-figure">{f}</figure>' for f in figs)
    return f"<style>{CSS if figs else ''}</style>{body}"


class TestValidatorFigureGates(unittest.TestCase):
    def test_clean_page_passes(self):
        self.assertEqual(vd.check_figures(page(BARS, LINE), 2), [])

    def test_no_figures_no_css_passes(self):
        self.assertEqual(vd.check_figures(page(), 2), [])

    def test_over_cap_fails(self):
        problems = vd.check_figures(page(BARS, BARS, BARS), 2)
        self.assertTrue(any("above the configured max_per_digest" in p for p in problems))

    def test_img_tag_fails(self):
        """Path B is entirely local; an <img> means a remote hotlink slipped in."""
        html = page(BARS).replace("</figure>", '<img src="https://x/y.jpg"></figure>')
        self.assertTrue(any("<img>" in p for p in vd.check_figures(html, 2)))

    def test_img_tag_detected_case_insensitively(self):
        html = page(BARS).replace("</figure>", '<IMG SRC="https://x/y.jpg"></figure>')
        self.assertTrue(any("<img>" in p for p in vd.check_figures(html, 2)))

    def test_bars_without_aria_label_fails(self):
        html = page(BARS.replace(' aria-label="a chart"', ""))
        self.assertTrue(any("aria-label" in p for p in vd.check_figures(html, 2)))

    def test_bars_with_empty_aria_label_fails(self):
        html = page(BARS.replace('aria-label="a chart"', 'aria-label=""'))
        self.assertTrue(any("aria-label" in p for p in vd.check_figures(html, 2)))

    def test_svg_without_role_fails(self):
        html = page(LINE.replace(' role="img"', ""))
        self.assertTrue(any('role="img"' in p for p in vd.check_figures(html, 2)))

    def test_svg_with_empty_title_fails(self):
        html = page(LINE.replace("<title id=\"t\">x</title>", '<title id="t"></title>'))
        self.assertTrue(any("<title>" in p for p in vd.check_figures(html, 2)))

    def test_figures_without_css_fails(self):
        html = f"<style></style><figure class=\"story-figure\">{BARS}</figure>"
        self.assertTrue(any("CSS block was stripped" in p
                            for p in vd.check_figures(html, 2)))

    def test_css_without_figures_fails(self):
        """The strip is what keeps a figures-off digest byte-identical to a
        pre-feature one, so leaving it in is a real regression."""
        html = f"<style>{CSS}</style>"
        self.assertTrue(any("should be stripped" in p
                            for p in vd.check_figures(html, 2)))

    def test_cap_read_from_config(self):
        self.assertEqual(vd.load_figure_cap(dirname(HERE)), 2)

    def test_cap_falls_back_when_config_unreadable(self):
        self.assertEqual(vd.load_figure_cap("/nonexistent"), 2)


class TestDocsMatchCode(unittest.TestCase):
    """Documentation that drifts from behaviour is worse than none -- the validator
    already shipped once claiming a config-driven range it did not read."""

    ROOT = dirname(HERE)

    def read(self, *parts):
        with open(join(self.ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_readme_documents_figures_and_keeps_the_stdlib_promise(self):
        readme = self.read("README.md")
        self.assertIn("## Figures", readme)
        self.assertIn("no third-party package", readme.lower().replace("**", ""))

    def test_readme_names_the_real_config_keys(self):
        readme = self.read("README.md")
        example = json.loads(self.read("_pipeline", "config.example.json"))
        for key in ("enabled", "max_per_digest"):
            self.assertIn(key, readme)
            self.assertIn(key, example["figures"])

    def test_setup_skill_asks_about_figures(self):
        skill = self.read("setup-news-digest", "SKILL.md")
        self.assertIn("figures.enabled", skill)
        self.assertIn("focus_state_tags", skill)

    def test_setup_template_matches_the_shipped_example(self):
        """A new user configured by the skill must get the same keys as a user who
        copies config.example.json by hand."""
        tpl = json.loads(self.read("setup-news-digest", "references",
                                   "config.template.json"))
        example = json.loads(self.read("_pipeline", "config.example.json"))
        for key in ("figures", "feedback_filename", "agent_candidates_per_topic",
                    "focus_state_tags"):
            self.assertIn(key, tpl, f"config.template.json missing {key}")
        self.assertIs(tpl["figures"]["enabled"], False)
        self.assertEqual(tpl["figures"], example["figures"])
        for t in tpl["topics"]:
            self.assertEqual(t.get("figures"), "none")

    def test_template_tags_cover_its_own_focus_states(self):
        tpl = json.loads(self.read("setup-news-digest", "references",
                                   "config.template.json"))
        for state in tpl.get("focus_states", []):
            self.assertIn(state, tpl.get("focus_state_tags", {}))

    def test_orchestrator_documents_rejection_logging(self):
        skill = self.read("SKILL.md")
        self.assertIn("figures_rejected", skill)
        self.assertIn("never a build failure", skill)


class TestCarryFields(unittest.TestCase):
    def test_figure_and_urls_are_carried(self):
        """A field absent from CARRY_FIELDS is dropped between the agent return and
        the stories JSON with no error -- the silent-failure point for this feature."""
        import assemble_stories as asm
        self.assertIn("figure", asm.CARRY_FIELDS)
        self.assertIn("urls", asm.CARRY_FIELDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
