# Repository Guidelines

## Project Structure & Module Organization

`diffido.py` contains the Tornado server, scheduling, fetching, Git history, and
email notifications. `dist/` holds maintained HTML templates and first-party
assets (`static/js/app.js`, `static/css/app.css`). `scripts/build-assets.cjs`
prepares external frontend assets. `conf/` contains configuration and email
templates; `storage/` contains runtime Git repositories. See `docs/DEVELOPMENT.md`
for API routes and `docs/DEPENDENCIES.md` for dependency ownership. There is no
automated test directory or CI workflow.

## Build, Test, and Development Commands

Run commands from the repository root; runtime paths are relative to it.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --ignore-scripts
npm run build
./diffido.py --debug
```

The server listens on port 3210. Node.js 22/npm are build tools only.
`docker build -t diffido:local .` builds the image; `./run-docker.sh` builds and
runs it with local configuration and storage mounts.

## Coding Style & Naming Conventions

Use four spaces for Python; prefer four spaces for JavaScript, HTML, and CSS.
Match surrounding code. Use Python `snake_case` functions and variables,
`PascalCase` classes, and uppercase constants. Document Python functions with
Sphinx-style docstrings. No formatter or linter is configured.

## Testing Guidelines

No testing framework or coverage threshold is configured; there is no `npm test`.
Use `node --check dist/static/js/app.js`, `npm run build`, and `git diff --check`
for relevant changes. Smoke-test server startup, affected API routes, and static
assets with disposable configuration and storage. For UI changes, check icons
and both diff layouts in a browser. Record commands and results in the PR.

## Commit & Pull Request Guidelines

History uses short, descriptive messages such as `remove obsolete dependencies`
and `fixes #19: fix rendering of diff pages`; follow that style and reference
issues when applicable. PRs should explain the problem, resulting behavior,
validation, and configuration or dependency changes. Include screenshots for
visible UI changes.

## Dependencies, Configuration & Agent Instructions

Declare dependencies in `requirements.txt` or `package.json`; update the npm
lockfile together. Never commit `node_modules/` or generated
`dist/static/vendor/`. Preserve upstream license notices when building assets.
Never commit credentials, TLS keys, schedule databases, or monitored content.
When `.codegraph/` exists, use `codegraph explore` before text searches or file
reads to locate and understand code. Do not create an index unless requested.
