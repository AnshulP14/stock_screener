---
name: setup-screener
description: Install this toolkit (data pipeline + screen-stocks/refresh-data skills) into another project so any coding agent can drive it from there. Use when asked to set up, install, add, or vendor the stock screener into a project.
---

# Vendoring the stock screener

This repo *is* the payload — no package registry step. Copy it into the target
project, register its two skills under the target's own skill directory, then let
`screen-stocks` bootstrap data on first use.

## 1. Copy the repo in

Copy everything except `.git/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, and the
gitignored contents of `data/` into `<target-project>/stock-screener/`. Two files
under `data/` are git-tracked exceptions (`SCHEMA.md`, `SQL.md`) and must survive the
copy — `screen-stocks` and `AGENTS.md` both point to them:

```bash
git clone --depth 1 <this-repo-url> /tmp/stock-screener-src
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.ruff_cache' \
  --include='data/SCHEMA.md' --include='data/SQL.md' --exclude='data/*' \
  /tmp/stock-screener-src/ <target-project>/stock-screener/
rm -rf /tmp/stock-screener-src
```

`screener/config.py` resolves every data path from its own file location, not the
caller's working directory — nothing under `scripts/` needs editing after the move.

## 2. Register the two skills

Copy `screen-stocks` and `refresh-data` into `<target-project>/.agents/skills/`,
rewriting their bare `scripts/...` / `data/...` / `AGENTS.md` path references (prose
and command examples only, never code) to `stock-screener/scripts/...` /
`stock-screener/data/...` / `stock-screener/AGENTS.md` — `screen-stocks/SKILL.md`'s
"preferred domains in AGENTS.md" line is one such bare reference. Symlink
`.claude/skills -> ../.agents/skills` for Claude Code, same convention this repo uses
for itself. Don't copy `setup-screener` itself — one-time setup skill, not needed by
the target project afterward.

## 3. Set up the environment

```bash
cd <target-project>/stock-screener && uv sync
```

Isolated `.venv` inside `stock-screener/` — doesn't touch or depend on whatever
toolchain the target project already uses.

## 4. First real use

No data ships with the toolkit. On the first `screen-stocks` invocation with nothing
in `stock-screener/data/`: auto-run quick mode (~5 min, top-50 NSE) without asking;
ask before a full bootstrap (60-90 min/market) — see `refresh-data`.

## Verify

```bash
cd <target-project> && stock-screener/.venv/bin/python3 stock-screener/scripts/query.py "SELECT 1"
```
