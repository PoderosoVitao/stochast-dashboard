# Stochast Dashboard

A live web dashboard for [stochast](https://github.com/PoderosoVitao/Stochast). Point it at a
scenario file and an adapter, hit start, and watch pass/fail results stream in as each run
finishes, instead of waiting for `stochast run` to end before you see anything.

This lives in its own repo on purpose. Stochast's own design spec rules out a web UI for the CLI
tool itself, so this dashboard doesn't touch that code. It just drives stochast's existing runner
as a library and shows what it already produces.

## Run it

```
uv sync
uv run stochast-dashboard
```

Then open `http://127.0.0.1:8000`, fill in a scenario path and an adapter spec (same format as
stochast's own `--adapter module:factory` or `--adapter file.py:factory`), and click Start.

**Security note:** an adapter spec loads and runs arbitrary local Python, just like stochast's CLI
`--adapter` flag does. The dashboard only binds to `127.0.0.1`. Don't expose it on a network.

## Scope

One run at a time. Starting a second run while one is active returns a 409. There's no
cancellation yet: that would need cooperative support inside stochast's runner, since Python can't
interrupt a worker thread mid-call, and a cancel button that can't actually stop a billed API call
would be worse than not having one. Each scenario's results still get persisted as JSON through
stochast's own `save_run_records`, so they stay usable by `stochast report` and `stochast compare`
afterward.

## Development

```
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy stochast_dashboard
```

Depends on `stochast` through an editable path dependency (`../Stochast`) during development.
