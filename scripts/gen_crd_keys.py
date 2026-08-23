#!/usr/bin/env python3
"""Regenerate scripts/crd-keys.json from checkouts of the repos that own the schemas.

    python3 scripts/gen_crd_keys.py <checkout> [<checkout> ...]

Each argument is a checkout of a tatara repo. Every repo that ships a chart contributes;
tatara-operator additionally contributes the rendered CRDs. A normal regeneration is:

    python3 scripts/gen_crd_keys.py \\
        ../tatara-operator ../tatara-memory \\
        ../tatara-memory-repo-ingester ../tatara-claude-code-wrapper

WHAT IS DERIVED, AND FROM WHICH ARTIFACT.

  crdProperties  every `properties:` name at every depth of
                 charts/*/crd-bases/*.yaml. The RENDERED CRD is deliberately the source
                 rather than the Go structs: it is what the apiserver installs and
                 therefore what actually decides pruning, so a field that exists in Go
                 but never reached the chart is correctly absent here. No Go parsing and
                 no guessing which struct is reachable from a spec.

  crdEnums       every `enum:` value in the same files. A reference table's leading
                 literal legitimately names either a field or one of its allowed values,
                 so check_crd_keys.py accepts an enum there (and only there).

  chartValues    for every chart under charts/: the keys of values.yaml at every depth,
                 UNION every `.Values.<path>` reference in templates/. The union matters:
                 `s3SecretName` is referenced by tatara-operator's deployment.yaml and
                 declared in no values.yaml, so a values.yaml-only derivation would
                 report a live, documented key as invented.

This is the only script here that needs PyYAML. check_crd_keys.py, the blocking gate,
reads the committed JSON and is stdlib-only. PyYAML is already pulled in by mkdocs and is
pinned explicitly in requirements.txt so that dependency is declared rather than
inherited.

FAILS LOUD ON A COLLAPSED DERIVATION. If a CRD layout changes under the walker, or a
checkout argument points somewhere with no charts, this exits 2 and writes nothing rather
than emitting a small snapshot. A snapshot that lost half its keys does not merely stop
checking - it turns the merge gate red on correct pages, which is the failure that trains
people to edit the guard.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - operator error, not a code path
    sys.exit(
        "gen_crd_keys: PyYAML is required. Run `mise run install` (it is pinned in "
        "requirements.txt), or `pip install pyyaml`."
    )

OUT = "scripts/crd-keys.json"

# Floors, not targets. Today's fleet derives roughly 540 properties, 106 enums and 274
# chart keys; these are set far below that so ordinary growth never trips them and only a
# structural collapse does.
MIN_PROPERTIES = 200
MIN_CHART_VALUES = 100


def die(msg: str) -> None:
    sys.exit(f"gen_crd_keys: {msg}")


def git(checkout: pathlib.Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(checkout), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""


def repo_name(checkout: pathlib.Path) -> str:
    url = git(checkout, "config", "--get", "remote.origin.url")
    if url:
        # owner/repo, from either https://host/owner/repo.git or git@host:owner/repo.git
        parts = url.removesuffix(".git").replace(":", "/").rstrip("/").split("/")
        if len(parts) >= 2:
            return "/".join(parts[-2:])
    return checkout.resolve().name


def walk_schema(node, props: set[str], enums: set[str]) -> None:
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                props.add(str(name))
                walk_schema(child, props, enums)
        allowed = node.get("enum")
        if isinstance(allowed, list):
            enums.update(str(v) for v in allowed if isinstance(v, str))
        for key, child in node.items():
            if key == "properties":
                continue
            if isinstance(child, (dict, list)):
                walk_schema(child, props, enums)
    elif isinstance(node, list):
        for child in node:
            walk_schema(child, props, enums)


def walk_values(node, keys: set[str]) -> None:
    if isinstance(node, dict):
        for name, child in node.items():
            keys.add(str(name))
            walk_values(child, keys)
    elif isinstance(node, list):
        for child in node:
            walk_values(child, keys)


def template_value_refs(templates: pathlib.Path, keys: set[str]) -> None:
    import re

    ref = re.compile(r"\.Values\.((?:[A-Za-z_]\w*)(?:\.[A-Za-z_]\w*)*)")
    for path in sorted(templates.rglob("*")):
        if not path.is_file():
            continue
        for match in ref.finditer(path.read_text(errors="ignore")):
            keys.update(match.group(1).split("."))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        die("usage: gen_crd_keys.py <checkout> [<checkout> ...]")

    props: set[str] = set()
    enums: set[str] = set()
    values: set[str] = set()
    sources: list[dict] = []

    for raw in argv[1:]:
        checkout = pathlib.Path(raw).expanduser()
        if not checkout.is_dir():
            die(f"{raw} is not a directory")

        charts = checkout / "charts"
        if not charts.is_dir():
            die(f"{raw} has no charts/ directory; it owns no schema this guard reads")

        paths: list[str] = []
        for crd in sorted(charts.glob("*/crd-bases/*.yaml")):
            walk_schema(yaml.safe_load(crd.read_text()), props, enums)
            paths.append(str(crd.relative_to(checkout)))
        for vals in sorted(charts.rglob("values.yaml")):
            walk_values(yaml.safe_load(vals.read_text()) or {}, values)
            paths.append(str(vals.relative_to(checkout)))
        for templates in sorted(charts.rglob("templates")):
            if templates.is_dir():
                template_value_refs(templates, values)
                paths.append(str(templates.relative_to(checkout)) + "/**")

        if not paths:
            die(f"{raw} has charts/ but nothing this guard reads under it")

        sources.append(
            {
                "repo": repo_name(checkout),
                "version": git(checkout, "describe", "--tags", "--abbrev=0") or "unknown",
                "commit": git(checkout, "rev-parse", "HEAD") or "unknown",
                "paths": paths,
            }
        )

    if len(props) < MIN_PROPERTIES:
        die(
            f"derived only {len(props)} CRD properties (floor {MIN_PROPERTIES}). The "
            "walker has lost the schema layout; refusing to write a collapsed snapshot."
        )
    if len(values) < MIN_CHART_VALUES:
        die(
            f"derived only {len(values)} chart values keys (floor {MIN_CHART_VALUES}). "
            "Pass every repo that ships a chart, not just tatara-operator."
        )

    root = pathlib.Path(__file__).resolve().parent.parent
    payload = {
        "provenance": {
            "generator": "scripts/gen_crd_keys.py",
            "generatedFrom": ", ".join(f"{s['repo']}@{s['version']}" for s in sources),
            "sources": sources,
        },
        "crdProperties": sorted(props),
        "crdEnums": sorted(enums),
        "chartValues": sorted(values),
    }
    (root / OUT).write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"wrote {OUT}: {len(props)} CRD properties, {len(enums)} enum values, "
        f"{len(values)} chart values keys, from {len(sources)} checkout(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
