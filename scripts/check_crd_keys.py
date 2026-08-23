#!/usr/bin/env python3
"""Fail when an in-nav docs page documents a Project/Task/Repository key that does not
exist in the CRD schema or in any chart's values surface.

WHY THIS EXISTS. The tatara CRDs are STRUCTURAL SCHEMAS, so the apiserver PRUNES an
unknown field: no error, no event, no log line. A maintainer who copies a documented
`spec.agent.maxHumanReviewRounds: 8` out of this site sees it in `helmfile diff`, sees a
clean apply, and gets whatever the operator compiled in. The docs are the only place that
failure is visible, and until this guard existed nothing here asked whether a documented
key was real. tatara-documentation#55 found two: `maxHumanReviewRounds`, which is a
package CONSTANT in tatara-operator (`api/v1alpha1/constants.go`) that this site
re-published as a settable field, and `maxConsecutiveSkips`, a circuit breaker the
operator retired outright (`internal/controller/proposalcount.go`).

The four gates that already run on every PR cannot see this class. `check-stale-terms.sh`
is a hand-maintained DENYLIST, so it only knows the terms somebody happened to notice;
`check_runbook_anchors.py` guards a different contract; `vale` is prose style and does not
even lint `reference/`; `mkdocs build --strict` resolves links, not claims. This is the
provenance guard every other repo in the fleet already has - tatara-observability's
`check_metric_provenance.py`, tatara-agent-skills' `validate_tool_calls.py` and
`validate_vocabulary.py` - pointed at the artifact that decides pruning.

OFFLINE AND STDLIB-ONLY, ON PURPOSE. It reads a committed snapshot
(`scripts/crd-keys.json`), never the network and never another checkout, so it can run on
a merge gate, in a pre-commit hook, and on a fork PR with no token. Currency of the
snapshot is a FLEET condition rather than a property of the PR in front of you, so it is
owned by the scheduled `.github/workflows/crd-keys-currency.yml` job, which clones
tatara-operator and opens a bump PR. That split is copied from tatara-agent-skills
(`lint.yml` tokenless over a committed snapshot, `vocabulary-currency.yml` scheduled with
the token); a blocking gate that clones a private repo is a gate that goes permanently red
the day the secret is not provisioned.

WHAT IS EXTRACTED, AND WHY NOT MORE. Two narrow scopes, measured against this corpus
before they were chosen (#55 pre-mortem 3):

  1. camelCase KEYS in fenced ```yaml / ```yml blocks. This is the copy-paste surface: it
     is what a maintainer actually pastes into a Project CR or a helmfile values file.
  2. The LEADING backticked literal of a markdown table row. This is the reference-table
     surface, where a field gets its type, default and description.

Prose is deliberately out of scope. Widening to every backticked token on every page pulls
in Go symbols, HTTP paths, CLI flags and ordinary English, and the guard degrades into a
waiver farm. A page may therefore NAME a retired key in a sentence without a waiver, which
is exactly how a retirement admonition should read.

MATCHING. A YAML key must match a CRD property name or a chart values key. A table literal
may additionally match a CRD ENUM VALUE, because a reference table's first cell documents
either a field or one of its allowed values. The fence gate stays strictly tighter than
the table gate for that reason.

TWO WAIVERS, AND BOTH MUST NAME WHAT THEY EXCUSE.

  - A line marker, for a single deliberate site - a migration table's "what this used to
    be" column, or a block that documents a key as removed:

        | `mrScan` | Removed at the 2026-07-13 redesign | <!-- crd-ok: mrScan -->

    The marker must NAME every key it exempts, comma-separated. A blanket
    `<!-- crd-ok -->` exempts NOTHING, so it cannot silently hide an unrelated invented
    key on the same line. Matching is case-insensitive and an HTML comment renders
    invisibly in mkdocs.

  - `foreignVocabularies` in `scripts/crd-keys-waivers.json`, for keys that belong to
    another schema entirely (Kubernetes core/batch/RBAC/networking, Helm). Every entry
    carries a REQUIRED, non-empty reason naming the schema it comes from; an entry with an
    empty or non-string reason is a hard configuration error, not a skipped check. This is
    where the guard would die if it were left unguarded (#55 pre-mortem 2): a waiver added
    to turn a red build green, with no reason attached, is how a check stops checking.

An UNUSED foreign-vocabulary waiver is REPORTED but does not fail. Deleting a paragraph
should not red CI in a file the author did not touch. Read the report and prune.

docs/appendix/** is never checked, structurally, the same way `check-stale-terms.sh`
excludes it. It is a dated archive of design documents; every one of them is a correct
snapshot of what was true on its date, and 20 of the stale identifiers on this site live
there legitimately (#55 pre-mortem 4).

WHAT THIS DELIBERATELY DOES NOT CHECK. Whether a key that DOES exist is described
correctly. `project.md` and `project-configuration.md` both document `Project`, and only
one of them tracked the brainstorm-breaker retirement; this guard would not have caught
that divergence and does not claim to. It answers exactly one question: does the apiserver
have somewhere to put this key.

Run: python3 scripts/check_crd_keys.py
Exit 0 = clean, 1 = undocumented keys found, 2 = usage/config error.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

SNAPSHOT = "scripts/crd-keys.json"
WAIVERS = "scripts/crd-keys-waivers.json"
EXCLUDED_DIR = "appendix"

# A camelCase identifier: starts lowercase, alphanumeric only, at least one interior
# capital. `spec`, `metadata` and SCREAMING_SNAKE env names are out by construction.
_CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")

_FENCE = re.compile(r"^\s*```(?P<info>.*)$")
_YAML_KEY = re.compile(r"^\s*(?:-\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*:(?:\s|$)")
# The first cell of a table row, when it holds exactly one backticked literal.
_TABLE_CELL = re.compile(r"^\s*\|\s*`(?P<lit>[^`]+)`\s*\|")
_MARKER = re.compile(r"<!--\s*crd-ok:(?P<body>[^>]*?)-->")


class WaiverError(Exception):
    """The waiver file is unusable. Never degrades into a skipped check."""


class Finding:
    def __init__(self, name: str, line: int, kind: str, text: str):
        self.name = name
        self.line = line
        self.kind = kind  # "yaml" or "table"
        self.text = text


def is_camel(token: str) -> bool:
    return bool(_CAMEL.match(token)) and any(c.isupper() for c in token)


def _fence_lang(info: str) -> str:
    """The language of a fence info string. ```yaml title="values.yaml" is yaml."""
    info = info.strip().strip("`").strip()
    return info.split()[0].lower() if info else ""


def extract_yaml_keys(markdown: str) -> list[Finding]:
    out: list[Finding] = []
    in_fence, lang = False, ""
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        fence = _FENCE.match(line)
        if fence:
            if in_fence:
                in_fence, lang = False, ""
            else:
                in_fence, lang = True, _fence_lang(fence.group("info"))
            continue
        if not in_fence or lang not in ("yaml", "yml"):
            continue
        m = _YAML_KEY.match(line)
        if m and is_camel(m.group("key")):
            out.append(Finding(m.group("key"), lineno, "yaml", line))
    return out


def extract_table_keys(markdown: str) -> list[Finding]:
    out: list[Finding] = []
    in_fence = False
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _TABLE_CELL.match(line)
        if not m:
            continue
        # `agent.maxHumanReviewRounds` documents the leaf, not the path.
        token = m.group("lit").strip().rstrip(".").split(".")[-1]
        if is_camel(token):
            out.append(Finding(token, lineno, "table", line))
    return out


def marker_names(line: str) -> set[str]:
    """Every key named by every `<!-- crd-ok: ... -->` on this line, lowercased.

    A marker that names nothing exempts nothing."""
    names: set[str] = set()
    for m in _MARKER.finditer(line):
        for part in m.group("body").split(","):
            part = part.strip().lower()
            if part:
                names.add(part)
    return names


def load_waivers(path: pathlib.Path) -> dict[str, str]:
    """The `foreignVocabularies` map, with every reason validated non-empty."""
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise WaiverError(f"{path}: {exc}") from exc

    raw = payload.get("foreignVocabularies", {})
    if not isinstance(raw, dict):
        raise WaiverError(f"{path}: foreignVocabularies must be an object")

    out: dict[str, str] = {}
    for key, reason in sorted(raw.items()):
        if not isinstance(reason, str) or not reason.strip():
            raise WaiverError(
                f"{path}: foreignVocabularies[{key!r}] needs a non-empty reason naming "
                "the schema the key belongs to. A waiver that does not say what it "
                "excuses is how this guard stops checking."
            )
        out[key] = reason.strip()
    return out


def pages(root: pathlib.Path) -> list[pathlib.Path]:
    docs = root / "docs"
    return sorted(
        p
        for p in docs.rglob("*.md")
        if EXCLUDED_DIR not in p.relative_to(docs).parts
    )


def check(
    root: pathlib.Path, snapshot: dict, waivers: dict[str, str]
) -> list[str]:
    """One problem string per undocumented key occurrence, most-specific first."""
    props = set(snapshot.get("crdProperties", []))
    values = set(snapshot.get("chartValues", []))
    enums = set(snapshot.get("crdEnums", []))
    waived = set(waivers)

    known = {"yaml": props | values, "table": props | values | enums}

    problems: list[str] = []
    for page in pages(root):
        rel = page.relative_to(root)
        markdown = page.read_text()
        findings = extract_yaml_keys(markdown) + extract_table_keys(markdown)
        for f in sorted(findings, key=lambda f: (f.line, f.name)):
            if f.name in known[f.kind] or f.name in waived:
                continue
            if f.name.lower() in marker_names(f.text):
                continue
            where = "a yaml block" if f.kind == "yaml" else "a reference table"
            problems.append(
                f"{rel}:{f.line}: `{f.name}` is documented in {where} but is not a "
                "field in any tatara CRD, not a key in any chart's values surface, and "
                "not a waived foreign vocabulary. The apiserver PRUNES it silently, so "
                "a maintainer who copies this gets no error and no effect.\n"
                f"      {f.text.strip()}\n"
                "      Fix the claim, or - if the page deliberately names a removed key "
                f"- add `<!-- crd-ok: {f.name} -->` on that line."
            )
    return problems


def unused_waivers(
    root: pathlib.Path, snapshot: dict, waivers: dict[str, str]
) -> list[str]:
    seen: set[str] = set()
    for page in pages(root):
        markdown = page.read_text()
        for f in extract_yaml_keys(markdown) + extract_table_keys(markdown):
            seen.add(f.name)
    return sorted(k for k in waivers if k not in seen)


def load_snapshot(path: pathlib.Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise WaiverError(f"{path}: {exc}") from exc
    for field in ("crdProperties", "chartValues"):
        if not isinstance(payload.get(field), list) or not payload[field]:
            raise WaiverError(
                f"{path}: {field} is missing or empty. Regenerate with "
                "scripts/gen_crd_keys.py; an empty snapshot would fail every page on "
                "this site rather than check it."
            )
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", default=SNAPSHOT)
    parser.add_argument("--waivers", default=WAIVERS)
    args = parser.parse_args(argv[1:])

    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        snapshot = load_snapshot(root / args.snapshot)
        waivers = load_waivers(root / args.waivers)
    except WaiverError as exc:
        print(f"check_crd_keys: {exc}", file=sys.stderr)
        return 2

    problems = check(root, snapshot, waivers)
    stale = unused_waivers(root, snapshot, waivers)

    if problems:
        print(f"FAIL: {len(problems)} documented key(s) that no schema has:\n")
        for p in problems:
            print(f"  - {p}")
        prov = snapshot.get("provenance", {})
        print(
            f"\nSnapshot: {args.snapshot} "
            f"({prov.get('generatedFrom', 'unknown provenance')})."
        )
        print(
            "If the key is real and new, the snapshot is stale: regenerate it with "
            "scripts/gen_crd_keys.py <checkout>... and say so in the PR."
        )
        return 1

    print(
        f"OK: every documented key resolves to a CRD field, a chart value, or a named "
        f"waiver ({len(waivers)} foreign vocabularies waived)."
    )
    if stale:
        print(
            f"\nnote: {len(stale)} foreign-vocabulary waiver(s) match nothing on the "
            "site any more and can be pruned (not a failure):"
        )
        for k in stale:
            print(f"  - {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
