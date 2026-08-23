#!/usr/bin/env python3
"""Self-tests for check_crd_keys.py.

The fixtures below are the reproduction from tatara-documentation#55: a page that
documents `maxHumanReviewRounds` (a package constant in tatara-operator, never a CRD
field) and `maxConsecutiveSkips` (a retired circuit breaker) must be REJECTED, and the
same page with the same identifiers named by a waiver must be ACCEPTED. A checker that
does not fail on the pre-fix corpus has not reproduced the issue.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import check_crd_keys as g
import check_crd_keys_currency as c


class ExtractYAMLKeys(unittest.TestCase):
    def test_reads_keys_only_from_yaml_fences(self):
        md = "\n".join(
            [
                "```yaml",
                "spec:",
                "  maxTurnsPerPod: 40",
                "```",
                "```go",
                "  notAKey: 1",
                "```",
                "```",
                "  alsoNotAKey: 1",
                "```",
                "prose mentioning inlineKey: 1",
            ]
        )
        self.assertEqual(
            {f.name for f in g.extract_yaml_keys(md)}, {"maxTurnsPerPod"}
        )

    def test_accepts_yml_and_a_titled_info_string(self):
        md = '```yml title="values.yaml"\nfooBar: 1\n```'
        self.assertEqual({f.name for f in g.extract_yaml_keys(md)}, {"fooBar"})

    def test_list_item_keys_are_extracted(self):
        # `items` is all-lowercase and therefore out of scope; the list ITEM's key is
        # the one a maintainer pastes into a CR.
        md = "```yaml\nitems:\n  - nameOfThing: a\n```"
        self.assertEqual({f.name for f in g.extract_yaml_keys(md)}, {"nameOfThing"})

    def test_lowercase_and_non_identifier_keys_are_ignored(self):
        # Only camelCase is in scope. `spec`, `metadata`, `apiVersion`-style
        # all-lowercase and SCREAMING_CASE env keys are not CRD field names and
        # pulling them in would make the guard a waiver farm (issue #55 pre-mortem 3).
        md = "```yaml\nspec: {}\nTATARA_FOO: 1\nkebab-case: 1\n```"
        self.assertEqual(g.extract_yaml_keys(md), [])

    def test_line_numbers_are_one_indexed_and_point_at_the_key(self):
        md = "intro\n\n```yaml\nmaxTurnsPerPod: 40\n```"
        self.assertEqual([f.line for f in g.extract_yaml_keys(md)], [4])


class ExtractTableKeys(unittest.TestCase):
    def test_leading_backticked_cell_of_a_table_row(self):
        md = "| Field | Type |\n|---|---|\n| `maxReviewRounds` | `int` |"
        self.assertEqual(
            {f.name for f in g.extract_table_keys(md)}, {"maxReviewRounds"}
        )

    def test_dotted_paths_reduce_to_their_last_segment(self):
        md = "| `agent.maxHumanReviewRounds` | `5` |"
        self.assertEqual(
            {f.name for f in g.extract_table_keys(md)}, {"maxHumanReviewRounds"}
        )

    def test_only_the_first_cell_is_read(self):
        # Widening past the first cell pulls in Go symbols, flags and English words.
        md = "| `realField` | see `inventedField` |"
        self.assertEqual({f.name for f in g.extract_table_keys(md)}, {"realField"})

    def test_a_first_cell_that_is_not_a_single_literal_is_ignored(self):
        md = "| `a`, `b` | x |\n| plain text | y |\n| `has space` | z |"
        self.assertEqual(g.extract_table_keys(md), [])

    def test_table_rows_inside_a_fence_are_ignored(self):
        md = "```markdown\n| `inventedField` | x |\n```"
        self.assertEqual(g.extract_table_keys(md), [])


class Markers(unittest.TestCase):
    def test_marker_must_name_the_key(self):
        self.assertEqual(g.marker_names("x <!-- crd-ok: mrScan -->"), {"mrscan"})

    def test_several_names_in_one_marker(self):
        self.assertEqual(
            g.marker_names("x <!-- crd-ok: aOne, bTwo -->"), {"aone", "btwo"}
        )

    def test_bare_marker_names_nothing(self):
        # A blanket waiver cannot silently hide an unrelated invented key.
        self.assertEqual(g.marker_names("x <!-- crd-ok -->"), set())
        self.assertEqual(g.marker_names("x <!-- crd-ok: -->"), set())

    def test_several_markers_on_one_line_all_count(self):
        self.assertEqual(
            g.marker_names("<!-- crd-ok: aOne --> <!-- crd-ok: bTwo -->"),
            {"aone", "btwo"},
        )


class Waivers(unittest.TestCase):
    def _write(self, payload):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "waivers.json"
        tmp.write_text(json.dumps(payload))
        return tmp

    def test_reason_is_required(self):
        p = self._write({"foreignVocabularies": {"podSelector": "k8s NetworkPolicy"}})
        self.assertEqual(g.load_waivers(p), {"podSelector": "k8s NetworkPolicy"})

    def test_empty_reason_is_rejected(self):
        p = self._write({"foreignVocabularies": {"podSelector": "   "}})
        with self.assertRaises(g.WaiverError):
            g.load_waivers(p)

    def test_non_string_reason_is_rejected(self):
        p = self._write({"foreignVocabularies": {"podSelector": True}})
        with self.assertRaises(g.WaiverError):
            g.load_waivers(p)


class Check(unittest.TestCase):
    SNAPSHOT = {
        "crdProperties": ["maxTurnsPerPod", "maxReviewRounds", "staleProposalDays"],
        "crdEnums": ["labeledOrMentioned"],
        "chartValues": ["ingressHost"],
    }

    def _run(self, pages, waivers=None):
        root = pathlib.Path(tempfile.mkdtemp())
        for rel, body in pages.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        return g.check(root, self.SNAPSHOT, waivers or {})

    def test_the_issue_55_reproduction_fails(self):
        problems = self._run(
            {
                "docs/reference/project.md": (
                    "| `maxHumanReviewRounds` | `int` | `5` | ... |\n"
                    "\n"
                    "```yaml\n"
                    "agent:\n"
                    "  maxTurnsPerPod: 40\n"
                    "  maxHumanReviewRounds: 5\n"
                    "```\n"
                ),
                "docs/operations/tuning.md": (
                    "```yaml\nbrainstorm:\n  maxConsecutiveSkips: 3\n```\n"
                ),
            }
        )
        blob = "\n".join(problems)
        self.assertIn("maxHumanReviewRounds", blob)
        self.assertIn("maxConsecutiveSkips", blob)
        self.assertNotIn("maxTurnsPerPod", blob)

    def test_a_named_line_marker_exempts_that_key_only(self):
        problems = self._run(
            {
                "docs/reference/project.md": (
                    "| `mrScan` | removed | <!-- crd-ok: mrScan -->\n"
                    "| `alsoInvented` | x | <!-- crd-ok: mrScan -->\n"
                )
            }
        )
        # Exactly one problem, and it is the key the marker did NOT name. The marker
        # text itself is echoed back in the offending line, so assert on the count and
        # the reported key rather than on substring absence.
        self.assertEqual(len(problems), 1)
        self.assertIn("`alsoInvented` is documented", problems[0])

    def test_a_foreign_vocabulary_waiver_exempts_everywhere(self):
        problems = self._run(
            {"docs/a.md": "```yaml\npodSelector: {}\n```"},
            waivers={"podSelector": "Kubernetes NetworkPolicy"},
        )
        self.assertEqual(problems, [])

    def test_appendix_is_never_checked(self):
        problems = self._run(
            {"docs/appendix/design-docs/2026-01-old.md": "```yaml\ninventedKey: 1\n```"}
        )
        self.assertEqual(problems, [])

    def test_crd_enums_satisfy_a_table_row_but_not_a_yaml_key(self):
        # A table's leading literal may name a field OR an enum VALUE. A YAML key is
        # only ever a field, so the fence gate stays strictly tighter.
        self.assertEqual(
            self._run({"docs/a.md": "| `labeledOrMentioned` | x |"}), []
        )
        self.assertEqual(
            len(self._run({"docs/a.md": "```yaml\nlabeledOrMentioned: 1\n```"})), 1
        )

    def test_chart_values_satisfy_a_yaml_key(self):
        self.assertEqual(self._run({"docs/a.md": "```yaml\ningressHost: x\n```"}), [])

    def test_a_problem_names_the_file_line_and_key(self):
        problems = self._run({"docs/a.md": "```yaml\ninventedKey: 1\n```"})
        self.assertEqual(len(problems), 1)
        self.assertIn("docs/a.md:2", problems[0])
        self.assertIn("inventedKey", problems[0])


class Currency(unittest.TestCase):
    """The comparator the scheduled bump job's verdict rests on."""

    def test_identical_key_sets_are_current(self):
        snap = {"crdProperties": ["a"], "crdEnums": [], "chartValues": ["b"]}
        self.assertEqual(c.compare(snap, dict(snap)), [])

    def test_provenance_alone_is_not_drift(self):
        # Every merge in any of the four repos moves provenance.commit. If that
        # counted, the job would force-push a no-op onto an open PR every night.
        old = {"crdProperties": ["a"], "provenance": {"sources": [{"commit": "aaa"}]}}
        new = {"crdProperties": ["a"], "provenance": {"sources": [{"commit": "bbb"}]}}
        self.assertEqual(c.compare(old, new), [])

    def test_an_added_key_drifts_and_is_named(self):
        drift = c.compare({"crdProperties": ["a"]}, {"crdProperties": ["a", "zNew"]})
        self.assertIn("crdProperties: +1 -0", drift)
        self.assertIn("  + zNew", drift)

    def test_a_removed_key_drifts_and_is_named(self):
        drift = c.compare({"chartValues": ["a", "gone"]}, {"chartValues": ["a"]})
        self.assertIn("chartValues: +0 -1", drift)
        self.assertIn("  - gone", drift)


if __name__ == "__main__":
    unittest.main()
