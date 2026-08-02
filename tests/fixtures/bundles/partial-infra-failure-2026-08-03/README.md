# Fixture: partial-infra-failure-2026-08-03

## What produced it

```
$ENV/bin/agentevalops run --config configs/toy_openai.yaml \
    --output runs/live-verify-openai-2026-08-03
```

A real, live attempt to verify `OpenAIRunner` (`docs/adr/0001-openai-runner-
chat-completions.md`) against the OpenAI Chat Completions API, using a valid
`OPENAI_API_KEY`. **Date: 2026-08-03.**

## Why it is incomplete

The OpenAI account backing the key had no billing credit. Every model call
failed immediately with `429 insufficient_quota` / `credit_balance_exhausted`.
`OpenAIRunner` correctly followed its documented contract — `core/errors.py`'s
`InfrastructureError` docstring: *"Agent-level failures ... are NOT this
exception"* — and raised `InfrastructureError` from inside `_call_model`
(`agents/openai_runner.py`) rather than swallowing the failure as a bad
answer.

But nothing catches that exception between the runner and the CLI:

- `LocalOrchestrator.run()` (`orchestration/local.py`) only catches
  `(KeyboardInterrupt, asyncio.CancelledError)` around
  `asyncio.gather(*futures)`. `InfrastructureError` is neither, so it
  propagates straight out, skipping the per-task result loop,
  `trace_store.finalize()`, `SimpleScorer().score(...)`, and
  `policy_checker.check(...)` — for **every** task in the run, not just the
  one that failed.
- `cli.py`'s `run` command catches `InfrastructureError` only around the
  top-level `asyncio.run(orchestrator.run())` call, prints
  `"Infrastructure error: {exc}"` to stderr, and exits 1 — *before*
  `_print_summary()` or `BundleWriter(...).write(...)` are ever reached.

The only reason `traces.jsonl` survives at all is that `LocalTraceStore`
flushes each event to disk as it streams, independently of the orchestrator
ever finishing successfully. Everything downstream of that — `metadata.json`,
`config.json`, `evaluations.json`, `policy.json`, `summary.json`, `report.md`,
`manifest.json` — was simply never written, because the code path that would
write them never ran.

This bundle is genuine evidence for the investigation recorded in
`ROADMAP.md` §0 (2026-08-03 entry) into whether the platform distinguishes
infrastructure failure from agent/evaluation failure — as of this fixture's
capture date, it does not: an infra failure during any task discards the
entire run's results rather than being scored, or even reported, as anything
at all.

## Content

`traces.jsonl` — two `agent.plan` events (one per toy `smoke`-scenario task),
no `cost.tick`, no `agent.terminal`. Confirmed to contain no API responses,
key material, or account identifiers before being committed (the only
substring match on a credential-shaped grep was `tokens_delta`, a schema
field name for a token *count*, not a token value).

## What a consumer should assert against it

This is not a well-formed bundle and must not be treated as one. The only
correct behavior is for bundle tooling to **reject** it, with a specific
reason — not merely a non-zero exit. See
`tests/test_bundle_fixtures.py::test_partial_infra_failure_fixture_fails_validation`,
which asserts `validate_bundle()` returns `valid=False` with exactly this
missing-file list:

```
metadata.json, config.json, evaluations.json, policy.json, summary.json,
report.md, and manifest.json (reported separately as "Missing manifest
file")
```

## This fixture must never be regenerated

It was captured from a real failure against a real (if underfunded) OpenAI
account, not synthesized. Regenerating it — e.g. by re-running the command
above, or by hand-crafting a new `traces.jsonl` to match whatever the current
code happens to produce — would make it worthless as a regression check: a
fixture that's updated to match current behavior can never catch current
behavior regressing. This is the same discipline as the project's
mutation-testing rule: a check that can't fail proves nothing. If the
orchestrator's error-handling is ever fixed (see the open ROADMAP gap), add a
**new**, separately-dated fixture demonstrating the fixed behavior — do not
edit this one to match it.
