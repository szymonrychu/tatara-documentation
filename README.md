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

## Contributing

All content is Markdown under `docs/`. The nav is defined in `mkdocs.yml`.
PRs welcome for corrections, new examples, and improved explainers.
