# tatara documentation

Source for the [tatara platform documentation site](https://szymonrychu.github.io/tatara-documentation/).

Tatara is a Kubernetes-native agentic development platform: a Kubernetes operator
that orchestrates autonomous agents (Claude Code) to triage issues, write code,
open PRs, review changes, and handle incidents - all driven by GitHub/GitLab webhooks
and a durable knowledge graph.

## Run locally

```sh
pip install -r requirements.txt
mkdocs serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Or with mise (pins Python version automatically):

```sh
mise install
pip install -r requirements.txt
mkdocs serve
```

## Build (strict)

```sh
mkdocs build --strict
```

Output lands in `site/`. The GitHub Actions workflow runs this on every PR and
deploys to GitHub Pages on merge to `main`.

## Checks

```sh
./scripts/check-stale-terms.sh                      # no pre-redesign terminology
python3 scripts/check_runbook_anchors.py            # runbook anchor contract
python3 -m unittest discover scripts -p 'test_*.py' # checker self-tests
```

`docs/operations/runbooks.md` is a cross-repo API: Grafana alert rules in
[tatara-observability](https://github.com/szymonrychu/tatara-observability) carry a
`runbook_url` annotation pointing at its `tatara-runbook-*` anchors, and the incident
agent follows that link on every incident turn. `mkdocs build --strict` validates
internal links only and cannot see an inbound link from another repo, so
`check_runbook_anchors.py` guards the anchor set instead: it is append-only against
`main`, and it prints the covered/total runbook count. The contract is documented at the
top of the runbooks page itself.

## Contributing

All content is Markdown under `docs/`. The nav is defined in `mkdocs.yml`.
PRs welcome for corrections, new examples, and improved explainers.
