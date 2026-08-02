# CLAUDE.md — AgentEvalOps

## Environment (read this before running anything)

This project uses an **existing conda environment. Do not create a venv, do not
create a new conda env, do not use `uv`.**

```
ENV=/Users/debdeeppaul/opt/anaconda3/envs/Sai2511
```

Invoke every tool by absolute path. Bare `python` / `pytest` / `mypy` / `ruff`
resolve off `$PATH`, fall outside this project's permission allowlist, and may
point at a different interpreter than the one the project is installed into.

| Task | Command |
|---|---|
| Sanity check | `$ENV/bin/python -c "import agentevalops; print(agentevalops.__version__)"` |
| Install | `$ENV/bin/pip install -e ".[dev]"` |
| Lint | `$ENV/bin/python -m ruff check src/ tests/` |
| Type-check | `$ENV/bin/python -m mypy src` |
| Test | `$ENV/bin/python -m pytest -q --cov=agentevalops --cov-report=term-missing` |
| Smoke | `$ENV/bin/agentevalops run --config configs/toy_smoke.yaml --output runs/tmp` then `validate-bundle` / `replay` on the same dir |

The `Makefile` targets invoke bare executables and are therefore only safe with
the env already activated. Prefer the absolute-path commands above.

## Local env vs CI — a known divergence

CI installs **only** `.[dev]` (see `.github/workflows/ci.yml`). Sai2511 is a
long-lived general-purpose env and may additionally have the optional `llm` and
`otel` extras installed. That divergence has already caused one production
break: a lazily-imported optional dependency type-checked clean locally and
failed `mypy --strict` in CI.

**Before trusting a local green gate on anything touching an optional import:**

```bash
$ENV/bin/pip show anthropic opentelemetry-api 2>&1 | grep -E "^(Name|WARNING)"
```

If either is present, your local `mypy src` is *more permissive* than CI and
cannot reproduce CI import errors. Verify against the Actions run, not locally.

**Sai2511 is also shared and mutable for core (non-extra) dependencies, not
just optional ones.** On 2026-08-02 the shared env was found downgraded to
`typer==0.12.0` by a sibling project's install — a version that fails
`RuntimeError: Type not yet supported` on every CLI command, since it has no
Optional/Union parameter support at all. 49 CLI tests failed. Nothing caught
this locally because nobody ran the pre-session check below; CI never resolves
this low because it always installs the newest satisfying version. Since then
`pyproject.toml` pins a real floor (`typer>=0.15.4,<1`, bisected — see the
comment there) and a `constraints-min.txt` + `ci-min-deps` CI job makes that
floor testable, but a *shared* env can still be downgraded below even a correct
floor by an unrelated project's install. Run this at the start of any session:

```bash
$ENV/bin/pip check
$ENV/bin/python -m pip list --format=freeze | grep -iE "^(typer|pyyaml|anthropic|pytest|mypy|ruff)="
```

Known-good, as of 2026-08-02: `typer==0.27.0`, `PyYAML==6.0.3`,
`anthropic==0.75.0`, `pytest==9.0.3`, `mypy==2.1.0`, `ruff==0.15.14`. If any of
these print something markedly older (especially `typer` below `0.15.4`),
suspect a sibling project's install and re-check before trusting any other
command in this file.

`pip check` will report pre-existing, unrelated breakage in this shared env —
notably `python-a2a 0.5.10 requires langchain, which is not installed`. That
belongs to an abandoned strategy in a different project and is not
agentevalops's problem; don't mistake it for something new, and don't fix it
here.

## Baseline (verified 2026-08-02, HEAD `eb99b66`)

- ruff: clean
- pytest: 571 passed, 1 skipped (opt-in `@pytest.mark.live`), 96% branch coverage
- CI floor: `--cov-fail-under=94`
- `mypy src`: **fails in CI** on `anthropic` import-not-found — see ROADMAP §0

## Optional extras

- `llm` — `anthropic`, for `agents/langgraph_runner.py`. Real runs cost money and
  need `ANTHROPIC_API_KEY`. Never run `configs/toy_langgraph.yaml` without being
  asked to.
- `otel` — scaffold only, emits nothing.