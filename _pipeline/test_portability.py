#!/usr/bin/env python3
"""
Guards against personal or machine-specific values creeping back into the shipped
engine. Run with:

    python3 -m unittest discover -s _pipeline -p 'test_*.py' -v

The scanner tests matter more than the unit tests here: the engine is published, so
a literal that is merely *correct for this install* is a defect for everyone else.
"""
import json
import os
import re
import subprocess
import sys
import unittest
from os.path import abspath, dirname, join

HERE = dirname(abspath(__file__))
ROOT = dirname(HERE)
sys.path.insert(0, HERE)

import build_context as bc      # noqa: E402
import validate_digest as vd    # noqa: E402


def tracked_engine_files():
    """Files git actually ships, minus generated output and personal state."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split("\n")
    except Exception:
        return []
    keep = []
    for f in out:
        if not f or f.startswith("_goldens/"):
            continue
        # This file necessarily contains the terms it searches for -- the patterns
        # below are literal owner names and path prefixes -- so scanning it would
        # always self-report. Everything else is fair game, including other tests.
        if os.path.basename(f) == os.path.basename(__file__):
            continue
        if f.endswith((".py", ".html", ".md", ".json")) and "example" not in f:
            keep.append(join(ROOT, f))
    return [f for f in keep if os.path.isfile(f)]


class TestNoPersonalDataInEngine(unittest.TestCase):
    """config.json / news_feedback.md / daily-topics.md are gitignored and hold the
    personal values. Nothing tracked should duplicate them."""

    # Identity and geography only. Outlet names are deliberately NOT listed: the
    # engine legitimately ships editorial defaults (websearch_regional_allowlist
    # carries ynet.co.il, presstv.ir, aljazeera.com and so on) that serve any user
    # covering that region. A personal *preference* for an outlet lives in the
    # gitignored config.json's official_sources; the shipped lists are generic.
    OWNER_TERMS = re.compile(
        r"\b(yossi|weihs|hudson|savannah|memphis|indianapolis|south holland|"
        r"hillsborough|shelby county|chatham county|marion county)\b", re.I)

    MACHINE_PATHS = re.compile(
        r"(/Users/|/home/[a-z]|C:\\\\|/sessions/[a-z]|Library/Application Support)")

    def _scan(self, pattern):
        """Return every (file, line-no, matched-text) hit, so a failure names all of
        them at once rather than only the first."""
        hits = []
        for path in tracked_engine_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    m = pattern.search(line)
                    if m:
                        hits.append(f"{os.path.relpath(path, ROOT)}:{i}: {m.group(0)!r}")
        return hits

    def test_no_owner_identifiers(self):
        hits = self._scan(self.OWNER_TERMS)
        self.assertEqual(hits, [], "personal values belong in the gitignored "
                                   "config.json, not in shipped files:\n  "
                                   + "\n  ".join(hits))

    def test_no_machine_specific_paths(self):
        hits = self._scan(self.MACHINE_PATHS)
        self.assertEqual(hits, [], "machine-specific paths in shipped files:\n  "
                                   + "\n  ".join(hits))

    def test_briefing_has_no_hardcoded_region_codes(self):
        """The return schema used to name one user's five US states, which every
        other user's agents were then told to emit."""
        with open(join(HERE, "briefing", "main.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("IL|IN|NH|TN|GA", text)
        self.assertIn("__FOCUS_STATES__", text)


class TestFocusStatesAreConfigDriven(unittest.TestCase):
    def test_tags_used_when_mapped(self):
        cfg = {"focus_states": ["Illinois", "Indiana"],
               "focus_state_tags": {"Illinois": "IL", "Indiana": "IN"}}
        self.assertEqual(bc._focus_states_token(cfg), "IL|IN")

    def test_falls_back_to_full_name_when_unmapped(self):
        cfg = {"focus_states": ["Bavaria", "Illinois"],
               "focus_state_tags": {"Illinois": "IL"}}
        self.assertEqual(bc._focus_states_token(cfg), "Bavaria|IL")

    def test_no_tags_map_at_all(self):
        self.assertEqual(bc._focus_states_token({"focus_states": ["Kent"]}), "Kent")

    def test_empty_config_gives_neutral_placeholder(self):
        self.assertEqual(bc._focus_states_token({}), "region tag")
        self.assertEqual(bc._focus_states_token({"focus_states": []}), "region tag")

    def test_ignores_blank_entries(self):
        self.assertEqual(
            bc._focus_states_token({"focus_states": ["  ", "Ohio", None]}), "Ohio")

    def test_not_us_specific(self):
        """Nothing in the resolver assumes US states."""
        cfg = {"focus_states": ["Kantō", "Kansai"],
               "focus_state_tags": {"Kantō": "KT"}}
        self.assertEqual(bc._focus_states_token(cfg), "KT|Kansai")


class TestStoryCountFromConfig(unittest.TestCase):
    """SKILL.md documents validation as running against config.story_count, but the
    range came from argparse defaults nothing overrode."""

    def test_reads_config(self):
        lo, hi, src = vd.load_story_count(ROOT)
        with open(join(HERE, "config.json"), encoding="utf-8") as fh:
            sc = json.load(fh)["story_count"]
        self.assertEqual((lo, hi), (sc["min"], sc["max"]))
        self.assertEqual(src, "config.story_count")

    def test_cli_overrides_config(self):
        lo, hi, src = vd.load_story_count(ROOT, 2, 3)
        self.assertEqual((lo, hi, src), (2, 3, "--min/--max"))

    def test_partial_cli_override(self):
        lo, hi, _ = vd.load_story_count(ROOT, None, 99)
        self.assertEqual(hi, 99)
        self.assertNotEqual(lo, 99)

    def test_missing_config_falls_back(self):
        lo, hi, src = vd.load_story_count("/nonexistent")
        self.assertEqual((lo, hi), (5, 8))
        self.assertEqual(src, "built-in default")


class TestConfigKeysExist(unittest.TestCase):
    """Every key the engine reads must be present in the shipped example, or a new
    user silently gets the in-code fallback instead of a documented setting."""

    def setUp(self):
        with open(join(HERE, "config.example.json"), encoding="utf-8") as fh:
            self.example = json.load(fh)

    def test_new_keys_documented_in_example(self):
        for key in ("feedback_filename", "agent_candidates_per_topic",
                    "focus_state_tags", "story_count"):
            self.assertIn(key, self.example, f"{key} missing from config.example.json")

    def test_new_keys_have_explanatory_comments(self):
        self.assertIn("_agent_candidates_per_topic_comment", self.example)
        self.assertIn("_focus_state_tags_comment", self.example)

    def test_example_tags_cover_example_states(self):
        for state in self.example.get("focus_states", []):
            self.assertIn(state, self.example.get("focus_state_tags", {}),
                          f"example focus_state_tags missing {state}")


class TestFeedbackFilenameSubstituted(unittest.TestCase):
    def test_template_uses_placeholder_not_literal(self):
        with open(join(HERE, "template.html"), encoding="utf-8") as fh:
            tpl = fh.read()
        self.assertIn("__FEEDBACK_FILE__", tpl)
        self.assertNotIn("'news_feedback.md'", tpl)

    def test_build_digest_substitutes_it(self):
        with open(join(HERE, "build_digest.py"), encoding="utf-8") as fh:
            self.assertIn("__FEEDBACK_FILE__", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
