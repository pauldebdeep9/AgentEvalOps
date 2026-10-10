# CLAUDE.md — AgentEvalOps

## Environment (read this before running anything)

This project uses an **existing conda environment. Do not create a venv, do not
create a new conda env, do not use `uv`.**

```
ENV=/Users/debdeeppaul/opt/anaconda3/envs/Sai2608
```

> Env moved from `Sai2511` to `Sai2608` on 2026-10-10. Passages below that name `Sai2511` describe incidents in that env and are kept as history.

Invoke every tool by absolute path. Bare `python` / `pytest` / `mypy` / `ruff`
resolve off `$PATH`, fall outside this project's permission allowlist, and may
point at a different interpreter than the one the project is installed into.

| Task | Command |
|---|---|
| Sanity check | `$ENV/bin/python -c "import agentevalops; print(agentevalops.__version__)"` |
| Install | `$ENV/bin/pip install -e ".[dev]"` |
| Lint | `$ENV/bin/python -m ruff check src/ tests/` |
| Type-check | `$ENV/bin/python -m mypy src` |
| Import layer contracts | `$ENV/bin/lint-imports` — **only the console script works.** `python -m importlinter.cli lint-imports` exits 0 with zero output regardless of contract state (`importlinter/cli.py` has no `if __name__ == "__main__":` guard); it silently checks nothing. |
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

Known-good, as of 2026-10-10 (Sai2608): `typer==0.27.1`, `PyYAML==6.0.3`,
`anthropic` not installed, `pytest==9.1.1`, `mypy==2.3.0`, `ruff==0.15.22`. If any of
these print something markedly older (especially `typer` below `0.15.4`),
suspect a sibling project's install and re-check before trusting any other
command in this file.

`pip check` was clean in `Sai2608` on 2026-10-10 ("No broken requirements
found"). In `Sai2511` it reported pre-existing, unrelated breakage —
`python-a2a 0.5.10 requires langchain, which is not installed`, from an
abandoned strategy in a different project. If `pip check` reports breakage
here, assume a sibling project's install first, report it, and don't fix it
in this repo.

## Baseline (verified 2026-10-10, Sai2608)

- ruff: clean
- mypy: clean (44 source files) — dedicated `anthropic.*` and `openai.*`
  mypy overrides in `pyproject.toml` (mirroring the pre-existing
  `opentelemetry.*` one) keep CI (which installs only `.[dev]`) green
  despite both SDKs being lazily-imported optional deps
- `lint-imports`: **7 kept, 0 broken** (7th contract added alongside
  row 14's SWE-bench spike: "swebench never imported")
- pytest: 705 passed, 1 skipped (opt-in `@pytest.mark.live`), 96.73% branch
  coverage on a clean checkout. Any untracked file raises it to 96.90%:
  tests reach the code-drift "inconclusive" warning (`bundles/validator.py`,
  `cli.py`) only through this checkout's own git state (`ROADMAP.md` §0,
  2026-10-10 finding). Compare against the clean-checkout figure.
- CI floor: `--cov-fail-under=94`
- Bundle format: `BUNDLE_FORMAT_VERSION = "0.4"` (`bundles/constants.py`);
  `"0.1"` bundles (incl. `runs/toy-smoke*` on disk) still validate/read —
  see `docs/adr/0002-outcome-taxonomy.md`

## Optional extras

- `llm` — `anthropic` (for `agents/langgraph_runner.py`) and `openai` (for
  `agents/openai_runner.py`, docs/adr/0001-openai-runner-chat-completions.md).
  Real runs cost money and need `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  respectively. Never run `configs/toy_langgraph.yaml` or
  `configs/toy_openai.yaml` without being asked to.
- `otel` — scaffold only, emits nothing.