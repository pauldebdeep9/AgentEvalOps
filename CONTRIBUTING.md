# Contributing to AgentEvalOps

Thank you for your interest in contributing.

---

## Local setup

```bash
git clone <repo>
cd AgentEvalOps
python -m pip install -e ".[dev]"
```

Requires Python 3.10+.

---

## Running tests

```bash
make test          # pytest
make test-cov      # pytest with coverage report
```

Or directly:

```bash
pytest
pytest --cov=agentevalops --cov-report=term-missing
```

---

## Lint and type-check

```bash
make lint          # ruff check src/ tests/
make typecheck     # mypy src
make check         # lint + typecheck + test
```

Or directly:

```bash
ruff check src/ tests/
mypy src
```

---

## Smoke test

After touching runtime code, run the full end-to-end smoke:

```bash
make smoke
```

This runs:

```bash
agentevalops run --config configs/toy_smoke.yaml --output runs/make-smoke
agentevalops validate-bundle --bundle runs/make-smoke
agentevalops replay --bundle runs/make-smoke
```

and then cleans up `runs/make-smoke`.

---

## Pre-commit hooks (optional)

```bash
pre-commit install
pre-commit run --all-files
```

Hooks: trailing whitespace, EOF newlines, YAML/TOML syntax, large-file guard, ruff.

---

## Coding style

- Ruff enforces `E`, `F`, `I` rules with `line-length = 88`.
- Mypy `strict = true` — all functions must be fully typed.
- No `# type: ignore` without a comment explaining why.
- Prefer explicit over clever.

---

## Scope discipline

AgentEvalOps is a **local-first evaluation framework**. The following are
explicitly out of scope until a design discussion occurs:

- Cloud backends (AWS Bedrock, any managed API)
- Model provider SDKs (OpenAI, Anthropic, LangGraph, Ollama)
- HTTP clients (`requests`, `httpx`, `urllib`) in runtime code
- `subprocess` calls in runtime code
- External benchmark downloads (SWE-bench, HumanEval, etc.)
- FastAPI dashboard or web UI
- Database persistence
- Plugin or extension framework

If your change requires any of these, open a feature request first.

---

## Generated artifacts

- `runs/` is git-ignored. Do not commit generated run outputs.
- `dist/` and `build/` are git-ignored. Do not commit package build outputs.
- Run `make clean` to remove caches and build artefacts.

---

## Adding a new toy scenario

1. Add a new scenario key to `ToyBenchmarkAdapter` in
   `src/agentevalops/benchmarks/toy.py`.
2. Add tasks to the scenario's task list.
3. Add a YAML config under `configs/` (use an existing config as a template).
4. Add tests under `tests/test_toy_scenarios.py`.
5. Document the scenario in `README.md` and `docs/local-demo.md`.

---

## Submitting a pull request

1. Fork and create a branch.
2. Run `make check` and `make smoke`.
3. Open a PR using the pull request template.
4. Ensure no generated `runs/`, `dist/`, or `build/` files are staged.
