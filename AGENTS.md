# AGENTS.md

Instructions for coding agents (Codex, Cursor, Gemini, Claude Code and others) working in this repository.

1. Read [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md): what the project is, what works, where things are, how
   work is done, how releases are built, and the backlog.
2. Follow [`CLAUDE.md`](CLAUDE.md): it is the rulebook for every change (read-only AWS gate, exact money, honest
   states, plain-text provider text, layer rules, Qt lessons, git workflow). It applies to every agent, not only Claude.
3. Before you finish: `uv run pytest -q -W error` is green and `uv run python -m tools.publish_check` prints
   `publish_check: OK`. Visible changes get Light, Dark and Dark-150 % goldens, and you look at every PNG.
