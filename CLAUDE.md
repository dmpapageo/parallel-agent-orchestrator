# CLAUDE.md

## What this is

Demonstrates parallel multi-agent orchestration. One orchestrator discovers which modules in `target_repo/` have failing pytest suites, spawns one sub-agent process per failing module, runs them concurrently, and reports a timeline, speedup and token usage. Each sub-agent is a hand-written Anthropic Messages API loop with exactly three tools (`read_file`, `write_file`, `run_tests`), scoped to a single module directory. Everything runs inside a Docker container with no internet route except a squid allowlist proxy to `api.anthropic.com`.

## Stack

- Python 3.12 (image: `python:3.12-slim`), stdlib only for the orchestration (`subprocess`, `argparse`, `dataclasses`).
- Deps (`requirements.txt`, pip): `pytest==8.3.4`, `anthropic` (unpinned).
- Docker + docker compose; squid (`ubuntu/squid:latest`) as egress proxy.
- CI: GitHub Actions (`.github/workflows/tests.yml`), push + PR.
- No JS/Node, no lockfile, no linter, no type checker in the repo.

## Commands

```bash
pip install -r requirements.txt          # install
python -m pytest tests/ -q               # test (exactly what CI runs; no API key or Docker needed)

export ANTHROPIC_API_KEY=sk-ant-...      # read from shell, never baked into the image
docker compose build
docker compose run --rm orchestrator python -m orchestrator.run            # full parallel run
docker compose run --rm orchestrator python -m orchestrator.run --logs     # per-agent transcripts
docker compose run --rm orchestrator python -m orchestrator.run --stream   # live interleaved output
docker compose run --rm orchestrator python -m agent.subagent --module pricing
docker compose down
```

## Layout & gotchas

- `agent/guards.py`: `ModuleScope` path allowlist (resolve-then-contain, not string matching), iteration cap, `is_green`. All safety decisions live here. `agent/tools.py`: the three tools. `agent/subagent.py`: the agent loop, CLI `--module`.
- `orchestrator/discover.py`: lists modules, runs each module's tests, decides failing by **exit code** (never by parsing pytest output). `orchestrator/run.py`: assign/start/wait/collect, CLI `--stream` / `--logs`. `orchestrator/report.py`: display only.
- `target_repo/{pricing,text_stats,scheduling,inventory}/`: fixtures with **deliberately planted bugs**. Do not "fix" them; failing tests there are the input to the system. Never run bare `pytest` at repo root: it collects `target_repo/` and fails by design. Scope to `tests/`.
- `tests/test_containment.py` is the only real test suite: it asserts the guards, the tool surface, the iteration cap, and the committed `proxy/squid.conf` + `docker-compose.yml` + `Dockerfile` (allowlist, `internal: true` network, non-root user, root-owned `agent/`). Editing those config files can break tests.
- Env vars: `ANTHROPIC_API_KEY` (required for real runs), `AGENT_MODEL`, `AGENT_MAX_ITERATIONS` (default 10; `0` makes a sub-agent stop before any API call), `AGENT_USAGE_DIR` (set by the orchestrator, inherited by children), `HTTPS_PROXY`/`HTTP_PROXY` (set by compose).
- `agent/` and `orchestrator/` are intentionally **not** chowned in the Dockerfile: root-owned so a sub-agent cannot rewrite its own loop or guards. Only `target_repo/` is agent-writable.
- Each `docker compose run` gets a fresh container, so fixes do not persist between commands; chain with `bash -c` to see a fix's effect.
- The live network containment check (proxy refuses `example.com`, allows `api.anthropic.com`) is a manual step described in the README; nothing automates it.
