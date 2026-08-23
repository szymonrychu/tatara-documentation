#!/usr/bin/env python3
"""Report whether scripts/crd-keys.json still matches the upstream schemas.

    python3 scripts/check_crd_keys_currency.py <checkout> [<checkout> ...]

Re-derives the snapshot from the given checkouts (the same derivation
gen_crd_keys.py performs, in memory) and diffs the KEY SETS against the committed
one. Used only by .github/workflows/crd-keys-currency.yml, which is scheduled and
holds the token; the blocking gate reads the committed snapshot and never runs
this.

PROVENANCE IS IGNORED BY THE COMPARISON, deliberately. gen_crd_keys.py stamps
provenance.commit with each checkout's HEAD, so a byte-level comparison would
report drift on every day any of the four repos merged anything, and the workflow
would force-push a provenance-only commit onto an open bump PR - resetting its
checks and any review on it. Under semver push-CD the day nothing moves is the
rare one. Only crdProperties, crdEnums and chartValues decide the verdict, and
those are the only fields check_crd_keys.py reads.

Exit 0 = current, 1 = drifted, 2 = the derivation collapsed (refuse to propose a
bump that would DELETE live keys), 3 = could not run.
"""

from __future__ import annotations

import json
import pathlib
import sys

COMPARED = ("crdProperties", "crdEnums", "chartValues")


def compare(old: dict, new: dict) -> list[str]:
    """Human-readable drift lines. Empty means current."""
    out: list[str] = []
    for field in COMPARED:
        added = sorted(set(new.get(field, [])) - set(old.get(field, [])))
        removed = sorted(set(old.get(field, [])) - set(new.get(field, [])))
        if not added and not removed:
            continue
        out.append(f"{field}: +{len(added)} -{len(removed)}")
        out += [f"  + {k}" for k in added]
        out += [f"  - {k}" for k in removed]
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 3

    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    try:
        import gen_crd_keys
    except ImportError as exc:
        print(f"check_crd_keys_currency: {exc}", file=sys.stderr)
        return 3

    try:
        committed = json.loads((root / "scripts" / "crd-keys.json").read_text())
    except (OSError, ValueError) as exc:
        print(f"check_crd_keys_currency: {exc}", file=sys.stderr)
        return 3

    # gen_crd_keys.main() writes the file and die()s (SystemExit) on a collapsed
    # derivation. Let it write - the workflow commits exactly what it wrote - and
    # map its refusal onto the SUSPECT verdict so the bump PR is never opened with
    # half a vocabulary in it.
    try:
        gen_crd_keys.main(["gen_crd_keys.py", *argv[1:]])
    except SystemExit as exc:
        if exc.code:
            print(f"check_crd_keys_currency: derivation refused: {exc.code}", file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001 - any generator fault is SUSPECT, not drift
        print(f"check_crd_keys_currency: derivation failed: {exc}", file=sys.stderr)
        return 2

    try:
        regenerated = json.loads((root / "scripts" / "crd-keys.json").read_text())
    except (OSError, ValueError) as exc:
        print(f"check_crd_keys_currency: {exc}", file=sys.stderr)
        return 3

    drift = compare(committed, regenerated)
    if not drift:
        print("OK: the committed snapshot matches the upstream schemas.")
        return 0

    print("DRIFTED: the committed snapshot no longer matches the upstream schemas.\n")
    for line in drift:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
