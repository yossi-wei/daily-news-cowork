#!/usr/bin/env python3
"""
Tests for render_chart.py. Pure stdlib unittest -- run with:

    python3 -m unittest discover -s _pipeline -p 'test_*.py' -v

The emphasis is deliberately lopsided toward *refusal*: the renderer's job in an
unattended 5am run is to reject bad agent output quietly, so the rejection paths
matter more than the happy path.
"""
import json
import subprocess
import sys
import unittest
from os.path import dirname, abspath, join

sys.path.insert(0, dirname(abspath(__file__)))
import render_chart as rc  # noqa: E402

SRC = "https://example.gov/reports/2026/q2-capex"
URLS = [SRC, "https://reuters.com/tech/some-article-2026-08-03"]


def fig(**over):
    base = {
        "kind": "chart",
        "chart_type": "bar",
        "series": [{"label": f"Q{i}", "value": v}
                   for i, v in enumerate([12.4, 15.1, 14.2, 19.8], start=1)],
        "series_unit": "billion USD",
        "series_source": SRC,
        "alt": "Capex rose across four quarters.",
        "caption": "Spending nearly doubled.",
    }
    base.update(over)
    return base


class TestValidateAccepts(unittest.TestCase):
    def test_valid_bar(self):
        self.assertEqual(rc.validate_figure(fig(), URLS), (True, None))

    def test_valid_line(self):
        self.assertEqual(rc.validate_figure(fig(chart_type="line"), URLS), (True, None))

    def test_source_matches_ignoring_scheme_www_and_query(self):
        f = fig(series_source="http://www.example.gov/reports/2026/q2-capex?utm=x")
        ok, reason = rc.validate_figure(f, URLS)
        self.assertTrue(ok, reason)

    def test_negative_values_allowed(self):
        f = fig(series=[{"label": "a", "value": -3}, {"label": "b", "value": 2},
                        {"label": "c", "value": -1}, {"label": "d", "value": 5}])
        self.assertTrue(rc.validate_figure(f, URLS)[0])


class TestValidateRefuses(unittest.TestCase):
    def assertRefused(self, f, needle, urls=URLS):
        ok, reason = rc.validate_figure(f, urls)
        self.assertFalse(ok, f"expected refusal, got accept ({needle})")
        self.assertIn(needle, reason)

    def test_not_a_dict(self):
        self.assertRefused("nope", "not an object")

    def test_wrong_kind(self):
        self.assertRefused(fig(kind="image"), "unsupported kind")

    def test_wrong_chart_type(self):
        self.assertRefused(fig(chart_type="pie"), "unsupported chart_type")

    def test_too_few_points(self):
        self.assertRefused(fig(series=fig()["series"][:2]), "below min_points")

    def test_too_many_points(self):
        pts = [{"label": str(i), "value": i} for i in range(40)]
        self.assertRefused(fig(series=pts), "above max_points")

    def test_flat_series(self):
        pts = [{"label": f"p{i}", "value": 7} for i in range(5)]
        self.assertRefused(fig(series=pts), "flat series")

    def test_nan_and_inf(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            pts = [{"label": "a", "value": 1}, {"label": "b", "value": 2},
                   {"label": "c", "value": 3}, {"label": "d", "value": bad}]
            self.assertRefused(fig(series=pts), "not a finite number")

    def test_bool_is_not_a_number(self):
        pts = [{"label": "a", "value": 1}, {"label": "b", "value": 2},
               {"label": "c", "value": 3}, {"label": "d", "value": True}]
        self.assertRefused(fig(series=pts), "not a finite number")

    def test_string_value(self):
        pts = [{"label": "a", "value": 1}, {"label": "b", "value": 2},
               {"label": "c", "value": 3}, {"label": "d", "value": "4"}]
        self.assertRefused(fig(series=pts), "not a finite number")

    def test_empty_label(self):
        pts = [{"label": "  ", "value": 1}, {"label": "b", "value": 2},
               {"label": "c", "value": 3}, {"label": "d", "value": 4}]
        self.assertRefused(fig(series=pts), "empty label")

    def test_overlong_label(self):
        pts = [{"label": "x" * 40, "value": 1}, {"label": "b", "value": 2},
               {"label": "c", "value": 3}, {"label": "d", "value": 4}]
        self.assertRefused(fig(series=pts), "exceeds")

    def test_empty_unit(self):
        self.assertRefused(fig(series_unit=""), "series_unit is empty")

    def test_empty_alt(self):
        self.assertRefused(fig(alt=""), "alt is empty")

    def test_missing_source(self):
        self.assertRefused(fig(series_source=None), "missing or not a deep link")

    def test_bare_domain_source_is_not_a_deep_link(self):
        self.assertRefused(fig(series_source="https://example.gov"),
                           "missing or not a deep link")

    def test_source_not_in_story_urls(self):
        """The load-bearing traceability gate: a chart may not cite a source the
        story itself does not."""
        self.assertRefused(fig(series_source="https://elsewhere.com/a/b"),
                           "not among the story's cited urls")

    def test_story_with_no_urls_refuses(self):
        self.assertRefused(fig(), "not among the story's cited urls", urls=[])


class TestHostileInput(unittest.TestCase):
    """Labels, units, alt and caption are untrusted agent output, and the bar
    renderer emits HTML directly into the card."""

    def test_script_tag_in_label_is_escaped(self):
        pts = [{"label": "<script>x</script>", "value": 1},
               {"label": "b", "value": 2}, {"label": "c", "value": 3},
               {"label": "d", "value": 4}]
        markup, reason = rc.render_figure(fig(series=pts), URLS)
        self.assertIsNotNone(markup, reason)
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_quote_breakout_in_alt_is_escaped(self):
        f = fig(alt='" onload="alert(1)')
        markup, _ = rc.render_figure(f, URLS)
        self.assertNotIn('onload="alert(1)"', markup)
        self.assertIn("&quot;", markup)

    def test_ampersand_and_angle_in_unit(self):
        markup, _ = rc.render_figure(fig(series_unit="R&D <spend>"), URLS)
        self.assertIn("R&amp;D", markup)
        self.assertNotIn("<spend>", markup)

    def test_caption_escaped(self):
        markup, _ = rc.render_figure(fig(caption="<b>bold</b>"), URLS)
        self.assertNotIn("<b>bold</b>", markup)

    def test_svg_line_escapes_labels(self):
        pts = [{"label": "<x>", "value": 1}, {"label": "b", "value": 2},
               {"label": "c", "value": 3}, {"label": "d", "value": 4}]
        markup, _ = rc.render_figure(fig(chart_type="line", series=pts), URLS)
        self.assertNotIn("<x>", markup)

    def test_rtl_and_unicode_labels_survive(self):
        pts = [{"label": "ינואר", "value": 1}, {"label": "פברואר", "value": 2},
               {"label": "מרץ", "value": 3}, {"label": "אפריל", "value": 4}]
        markup, reason = rc.render_figure(fig(series=pts), URLS)
        self.assertIsNotNone(markup, reason)
        self.assertIn("ינואר", markup)


class TestRenderGeometry(unittest.TestCase):
    def test_bar_widths_are_zero_origin_proportions(self):
        pts = [{"label": "a", "value": 10}, {"label": "b", "value": 20},
               {"label": "c", "value": 40}, {"label": "d", "value": 5}]
        markup, _ = rc.render_figure(fig(series=pts), URLS)
        for pct in ("25.0%", "50.0%", "100.0%", "12.5%"):
            self.assertIn(f"width:{pct}", markup)

    def test_negative_bar_clamps_to_zero_width(self):
        pts = [{"label": "a", "value": -5}, {"label": "b", "value": 10},
               {"label": "c", "value": 20}, {"label": "d", "value": 40}]
        markup, _ = rc.render_figure(fig(series=pts), URLS)
        self.assertIn("width:0.0%", markup)

    def test_line_point_count_matches_series(self):
        pts = [{"label": f"p{i}", "value": i * 1.5} for i in range(6)]
        markup, _ = rc.render_figure(fig(chart_type="line", series=pts), URLS)
        self.assertEqual(markup.count("<circle"), 6)

    def test_line_endpoints_labelled(self):
        pts = [{"label": "Jan", "value": 1}, {"label": "Feb", "value": 4},
               {"label": "Mar", "value": 2}, {"label": "Dec", "value": 9}]
        markup, _ = rc.render_figure(fig(chart_type="line", series=pts), URLS)
        self.assertIn(">Jan<", markup)
        self.assertIn(">Dec<", markup)

    def test_accessible_names_present(self):
        bar, _ = rc.render_figure(fig(), URLS)
        self.assertIn('role="img"', bar)
        self.assertIn('aria-label=', bar)
        line, _ = rc.render_figure(fig(chart_type="line"), URLS)
        self.assertIn('role="img"', line)
        self.assertIn("<title", line)

    def test_svg_title_id_is_unique_per_story(self):
        a, _ = rc.render_figure(fig(chart_type="line"), URLS, uid="s1")
        b, _ = rc.render_figure(fig(chart_type="line"), URLS, uid="s2")
        self.assertIn('id="s1-t"', a)
        self.assertIn('id="s2-t"', b)

    def test_caption_optional(self):
        markup, _ = rc.render_figure(fig(caption=None), URLS)
        self.assertNotIn("fig-caption", markup)

    def test_deterministic(self):
        a, _ = rc.render_figure(fig(), URLS)
        b, _ = rc.render_figure(fig(), URLS)
        self.assertEqual(a, b)


class TestNumberFormatting(unittest.TestCase):
    def test_locale_neutral(self):
        self.assertEqual(rc.fmt(1234567.0), "1.23457e+06")
        self.assertEqual(rc.fmt(12.0), "12")
        self.assertEqual(rc.fmt(12.40), "12.4")
        self.assertEqual(rc.fmt(-0.5), "-0.5")

    def test_no_thousands_separator(self):
        self.assertNotIn(",", rc.fmt(1000.0))


class TestNeverRaises(unittest.TestCase):
    """A malformed figure must degrade to a logged refusal, never an exception --
    a bad figure may not take down the digest."""

    def test_garbage_shapes(self):
        for bad in (None, [], 42, "", {"kind": "chart"},
                    {"kind": "chart", "chart_type": "bar", "series": [None] * 5}):
            markup, reason = rc.render_figure(bad, URLS)
            self.assertIsNone(markup)
            self.assertTrue(reason)

    def test_series_of_wrong_shape(self):
        markup, reason = rc.render_figure(
            fig(series=[{"nope": 1}] * 5), URLS)
        self.assertIsNone(markup)
        self.assertIn("empty label", reason)


class TestCLI(unittest.TestCase):
    SCRIPT = join(dirname(abspath(__file__)), "render_chart.py")

    def test_cli_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p, out = join(d, "f.json"), join(d, "f.html")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(fig(), fh)
            r = subprocess.run([sys.executable, self.SCRIPT, "--in", p, "--out", out],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("fig-bars", fh.read())

    def test_cli_exits_nonzero_on_refusal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = join(d, "f.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(fig(series_unit=""), fh)
            r = subprocess.run([sys.executable, self.SCRIPT, "--in", p],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("series_unit is empty", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
