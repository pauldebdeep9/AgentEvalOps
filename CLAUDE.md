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