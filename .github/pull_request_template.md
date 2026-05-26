## Summary

<!-- One sentence: what does this PR do? -->

## Scope

<!-- Does this change runtime behaviour, tests, docs, CI, or packaging only? -->

## Tests run

<!-- Check all that apply. -->

- [ ] `make lint` — ruff passes
- [ ] `make typecheck` — mypy passes
- [ ] `make test` — all tests pass
- [ ] `make smoke` — run / validate-bundle / replay all pass (if touching runtime code)

## Checklist

- [ ] No generated `runs/` artifacts committed
- [ ] No `dist/` or `build/` artifacts committed
- [ ] No cloud/model/API dependencies added (boto3, openai, anthropic, requests, httpx, etc.) unless explicitly intended
- [ ] `ruff check src/ tests/` passes
- [ ] `mypy src` passes
- [ ] `pytest` passes
- [ ] PR title is concise and describes the change
