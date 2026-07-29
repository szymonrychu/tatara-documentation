#!/usr/bin/env python3
"""Tests for check_runbook_anchors. Run from the repo root:
python3 -m unittest discover scripts -p 'test_*.py'

No git calls: the append-only check is exercised by passing a base id set directly."""

import pathlib
import unittest

from check_runbook_anchors import (
    check_append_only,
    check_wellformed,
    coverage,
    parse_anchors,
    slugify,
)

GOOD = (
    '<a id="tatara-runbook-operator-gc-blocked"></a>'
    '<!-- alert: "Operator GC blocked" status: covered -->\n'
    '<a id="tatara-runbook-operator-sweep-erroring"></a>'
    '<!-- alert: "Operator sweep erroring" status: none -->\n'
    "## Ownership or GC invariant broken\n"
)


class SlugifyTest(unittest.TestCase):
    def test_matches_the_observability_side_contract(self):
        self.assertEqual(slugify("Operator GC blocked"), "operator-gc-blocked")
        self.assertEqual(
            slugify("Wrapper commit/push failure ratio high"),
            "wrapper-commit-push-failure-ratio-high",
        )
        self.assertEqual(
            slugify("Agent token spend runaway ($/s)"), "agent-token-spend-runaway-s"
        )
        self.assertEqual(
            slugify("Tier-quality rubber-stamp (model=claude-sonnet-5)"),
            "tier-quality-rubber-stamp-model-claude-sonnet-5",
        )


class ParseTest(unittest.TestCase):
    def test_parses_id_alert_and_status(self):
        anchors = parse_anchors(GOOD)
        self.assertEqual(
            [a.id for a in anchors],
            [
                "tatara-runbook-operator-gc-blocked",
                "tatara-runbook-operator-sweep-erroring",
            ],
        )
        self.assertEqual([a.status for a in anchors], ["covered", "none"])
        self.assertEqual([a.line for a in anchors], [1, 2])


class WellformedTest(unittest.TestCase):
    def test_good_page_has_no_problems(self):
        self.assertEqual(check_wellformed(GOOD), [])

    def test_anchor_with_no_marker_is_reported(self):
        problems = check_wellformed('<a id="tatara-runbook-orphan"></a>\n')
        self.assertEqual(len(problems), 1)
        self.assertIn("is not followed by a valid marker", problems[0])

    def test_anchor_with_an_invalid_status_is_reported(self):
        problems = check_wellformed(
            '<a id="tatara-runbook-x"></a><!-- alert: "X" status: maybe -->\n'
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("is not followed by a valid marker", problems[0])

    def test_duplicate_id_is_reported(self):
        problems = check_wellformed(GOOD + GOOD)
        self.assertTrue(any("declared twice" in p for p in problems))

    def test_id_that_does_not_match_its_alert_name_is_reported(self):
        problems = check_wellformed(
            '<a id="tatara-runbook-something-else"></a>'
            '<!-- alert: "Operator GC blocked" status: covered -->\n'
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("does not match its own alert name", problems[0])
        self.assertIn("tatara-runbook-operator-gc-blocked", problems[0])

    def test_a_non_runbook_anchor_is_ignored(self):
        self.assertEqual(check_wellformed('<a id="some-other-anchor"></a>\n'), [])

    def test_an_example_inside_a_code_fence_is_not_a_declaration(self):
        # The page documents the contract with a worked example. Counting it would make
        # documenting the format a duplicate-anchor failure.
        page = GOOD + "```html\n" + GOOD + "```\n"
        self.assertEqual(check_wellformed(page), [])
        self.assertEqual(len(parse_anchors(page)), 2)


class AppendOnlyTest(unittest.TestCase):
    def test_unchanged_set_passes(self):
        base = {a.id for a in parse_anchors(GOOD)}
        self.assertEqual(check_append_only(GOOD, base), [])

    def test_adding_an_anchor_passes(self):
        base = {"tatara-runbook-operator-gc-blocked"}
        self.assertEqual(check_append_only(GOOD, base), [])

    def test_removing_an_anchor_fails(self):
        base = {a.id for a in parse_anchors(GOOD)} | {"tatara-runbook-deleted-rule"}
        problems = check_append_only(GOOD, base)
        self.assertEqual(len(problems), 1)
        self.assertIn("tatara-runbook-deleted-rule", problems[0])

    def test_renaming_an_anchor_reads_as_a_removal(self):
        base = {"tatara-runbook-operator-gc-blocked-old"}
        problems = check_append_only(GOOD, base)
        self.assertEqual(len(problems), 1)
        self.assertIn("tatara-runbook-operator-gc-blocked-old", problems[0])


class CoverageTest(unittest.TestCase):
    def test_counts_covered_against_total(self):
        covered, total, placeholders = coverage(GOOD)
        self.assertEqual((covered, total), (1, 2))
        self.assertEqual(placeholders, ["Operator sweep erroring"])


class LivePageTest(unittest.TestCase):
    """The shipped runbooks.md must satisfy the contract it documents."""

    def test_shipped_page_is_wellformed(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        markdown = (root / "docs/operations/runbooks.md").read_text()
        self.assertEqual(check_wellformed(markdown), [])
        _, total, _ = coverage(markdown)
        self.assertGreater(total, 0, "runbooks.md declares no anchors at all")


if __name__ == "__main__":
    unittest.main()
