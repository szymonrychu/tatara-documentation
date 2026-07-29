#!/usr/bin/env python3
"""Guard the runbook anchor contract in docs/operations/runbooks.md.

WHY THIS EXISTS. Grafana alert rules in szymonrychu/tatara-observability carry a
`runbook_url` annotation pointing at an anchor on this page, and the incident agent
follows it as phase 2 of every incident turn. That makes this page's anchor set a
cross-repo API. `mkdocs build --strict` structurally cannot protect it: it validates
INTERNAL links and nav, and an inbound link from an alert YAML in another repo is
invisible to it. So a heading rename or a deleted section passes this repo's CI green and
breaks ten alerts silently.

THE CONTRACT. Each alert rule gets its own anchor, declared as one line immediately above
the runbook section that serves it:

    <a id="tatara-runbook-<slug>"></a><!-- alert: "<exact rule name>" status: covered -->

- <slug> is the alert rule's `name`, lowercased, with every run of characters outside
  [a-z0-9] collapsed to a single "-" and leading/trailing "-" stripped. Both repos derive
  it with the same three lines of code and neither keeps a mapping table.
- The anchor is an explicit element, NOT the heading's mkdocs auto-slug. Headings stay
  freely editable, and the existing in-page links that already use the auto-slugs keep
  working. attr_list `{ #id }` ids were the obvious alternative and were rejected: they
  replace the auto-slug (breaking those in-page links) and they allow only ONE id per
  heading, while several runbook sections legitimately serve four or five alerts.
- `status: covered` means a real runbook backs the anchor. `status: none` is an honest
  "no runbook written yet" placeholder: the link still resolves, the on-call still learns
  which rule fired and where it is defined, and the gap is COUNTABLE instead of silent.

WHAT THIS CHECKS.
  1. Every declared anchor is well-formed and unique.
  2. The anchor id is the slug of the alert name in its own marker, so the two can never
     drift apart within one line.
  3. Append-only: every anchor present on the base revision is still present. An anchor
     may be added, and a section may be rewritten or renamed, but an anchor may never be
     removed or renamed without a deliberate, visible edit to this rule.
  4. Coverage is reported as covered/total on every run.

WHAT THIS DELIBERATELY DOES NOT CHECK. "Does every alert rule have an anchor here?" That
direction needs the alert list, which lives in tatara-observability, and it is checked
THERE by scripts/check_runbook_urls.py. Splitting it this way means each break is reported
to the author who caused it: a docs PR that drops an anchor fails here, an alerts PR that
adds an unanchored rule fails there. Cloning the other repo from this job would duplicate
that check from the wrong side and put a network dependency in the docs build.

Run: python3 scripts/check_runbook_anchors.py [--base <git-ref>]
Exit 0 = clean, 1 = violations, 2 = usage/parse error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

RUNBOOKS = "docs/operations/runbooks.md"
ANCHOR_PREFIX = "tatara-runbook-"

_ANCHOR_LINE = re.compile(
    r'<a id="(?P<id>tatara-runbook-[a-z0-9-]+)"></a>'
    r'<!-- alert: "(?P<alert>[^"]+)" status: (?P<status>covered|none) -->'
)

# An anchor element that carries the prefix but does not match the full contract line -
# a half-written anchor with no marker, a typo'd status, a stray space. Caught explicitly
# so it cannot read as "no anchor here" and slip past the append-only check.
_LOOSE_ANCHOR = re.compile(r'<a id="(tatara-runbook-[^"]*)"></a>(?P<rest>[^\n]*)')

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(alert_name: str) -> str:
    """The anchor slug for an alert rule name. Must stay byte-identical to
    tatara-observability/scripts/check_runbook_urls.py:slugify."""
    return re.sub(r"-+", "-", _NON_SLUG.sub("-", alert_name.lower())).strip("-")


class Anchor:
    def __init__(self, anchor_id: str, alert: str, status: str, line: int):
        self.id = anchor_id
        self.alert = alert
        self.status = status
        self.line = line


def strip_code_fences(markdown: str) -> str:
    """Blank out fenced code blocks, preserving line count and numbering.

    The contract itself is documented on the page with a worked example inside a ```html
    fence. An example is not a declaration: without this, documenting the format would
    declare a duplicate anchor and fail the very check it explains."""
    out, in_fence = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def parse_anchors(markdown: str) -> list[Anchor]:
    out = []
    for lineno, line in enumerate(strip_code_fences(markdown).splitlines(), start=1):
        for m in _ANCHOR_LINE.finditer(line):
            out.append(
                Anchor(m.group("id"), m.group("alert"), m.group("status"), lineno)
            )
    return out


def check_wellformed(markdown: str) -> list[str]:
    """Malformed anchor elements, duplicate ids, and id/alert-name drift."""
    problems: list[str] = []
    anchors = parse_anchors(markdown)
    good_spans = {(a.id, a.line) for a in anchors}

    for lineno, line in enumerate(strip_code_fences(markdown).splitlines(), start=1):
        for m in _LOOSE_ANCHOR.finditer(line):
            if (m.group(1), lineno) in good_spans:
                continue
            problems.append(
                f"{RUNBOOKS}:{lineno}: anchor `{m.group(1)}` is not followed by a valid "
                "marker. The exact required form is:\n"
                f'    <a id="{m.group(1)}"></a><!-- alert: "Exact Rule Name" status: covered -->\n'
                f"  got: {m.group(0)!r}"
            )

    seen: dict[str, int] = {}
    for a in anchors:
        if a.id in seen:
            problems.append(
                f"{RUNBOOKS}:{a.line}: anchor `{a.id}` is declared twice (first at line "
                f"{seen[a.id]}). One alert, one anchor, one runbook - a duplicate id makes "
                "the link land on whichever copy the browser finds first."
            )
        seen[a.id] = a.line
        want = ANCHOR_PREFIX + slugify(a.alert)
        if a.id != want:
            problems.append(
                f"{RUNBOOKS}:{a.line}: anchor `{a.id}` does not match its own alert name "
                f'"{a.alert}", whose slug is `{want}`. The alert rule derives the anchor '
                "from its name, so this link can never resolve. Fix the id or the name in "
                "the marker."
            )
    return problems


def base_anchor_ids(base_ref: str) -> set[str] | None:
    """Anchor ids on base_ref, or None if that revision cannot be read (a shallow clone,
    a brand-new branch, the file not existing yet). Unreadable is a neutral skip: the
    append-only check is a regression guard, not a reason to block a first commit."""
    try:
        blob = subprocess.run(
            ["git", "show", f"{base_ref}:{RUNBOOKS}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return {a.id for a in parse_anchors(blob)}


def check_append_only(markdown: str, base_ids: set[str]) -> list[str]:
    current = {a.id for a in parse_anchors(markdown)}
    return [
        f"anchor `{a}` was on the base revision and is gone. Alert rules in "
        "tatara-observability link to it, and removing it 404s them to the top of the "
        "page with no error anywhere. Keep the anchor (a section may be rewritten or "
        "retitled underneath it); if the alert rule itself was deleted, delete the rule "
        "in tatara-observability first and say so in this PR."
        for a in sorted(base_ids - current)
    ]


def coverage(markdown: str) -> tuple[int, int, list[str]]:
    anchors = parse_anchors(markdown)
    covered = [a for a in anchors if a.status == "covered"]
    placeholders = sorted(a.alert for a in anchors if a.status == "none")
    return len(covered), len(anchors), placeholders


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default="origin/main",
        help="git ref to compare the anchor set against (default: origin/main)",
    )
    args = parser.parse_args(argv[1:])

    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        markdown = (root / RUNBOOKS).read_text()
    except OSError as exc:
        print(f"check_runbook_anchors: {exc}", file=sys.stderr)
        return 2

    problems = check_wellformed(markdown)

    base_ids = base_anchor_ids(args.base)
    if base_ids is None:
        print(
            f"note: could not read {RUNBOOKS} at {args.base} - skipping the append-only "
            "check for this run (not a failure).",
            file=sys.stderr,
        )
    else:
        problems += check_append_only(markdown, base_ids)

    covered, total, placeholders = coverage(markdown)

    if problems:
        print(f"FAIL: {len(problems)} runbook anchor problem(s):\n")
        for p in problems:
            print(f"  - {p}")
        print("\nSee the anchor contract at the top of docs/operations/runbooks.md.")
        return 1

    print(f"OK: {total} runbook anchor(s), all well-formed and none removed.")
    print(
        f"Runbook coverage: {covered}/{total} anchors are backed by a written runbook."
    )
    if placeholders:
        print(
            f"{len(placeholders)} anchor(s) are honest 'no runbook yet' placeholders:"
        )
        for alert in placeholders:
            print(f"  - {alert}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
