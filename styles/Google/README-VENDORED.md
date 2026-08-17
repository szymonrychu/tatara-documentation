# Vendored: errata-ai/Google

Checked in, not synced. `vale sync` would make every CI run depend on GitHub's
release CDN and on whatever `latest` points at that morning, so a style-package
bump could red a PR that changed nothing. There is no `Packages =` line in
`.vale.ini` and CI never runs `vale sync`.

Vendored 2026-08-17 from https://github.com/errata-ai/Google/releases/latest
(38 files, ~18 KB zipped: `meta.json`, `vocab.txt`, and 36 `.yml` rules - one
fewer rule file than the 37 the plan recorded, because upstream trimmed a rule
between the plan being written and this vendoring). To update: re-download
`Google.zip`, unzip over this directory, run `mise exec -- vale docs/`, and fix
or disable whatever the new rules find in the SAME commit.
