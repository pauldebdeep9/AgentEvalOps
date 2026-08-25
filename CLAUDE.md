# CLAUDE.md

Operating constraints for agentic work on this repo. Read `DESIGN.md` for what
this project is and `WBS.md` for what to build next.

---

## The one rule

**Do not add an enum member, dataclass field, protocol, config key, or module
that nothing in the package constructs.**

This project is an exercise in subtraction. The default gradient of a coding
agent is addition — adding `AGENT_OBSERVATION` back, adding retry logic, adding
a `TraceStore` protocol because there might one day be two stores. Every such
addition is a regression here, even when it is good code.

If a WBS item seems to need new vocabulary, say so and stop. Do not add it and
mention it afterwards.

## Scope discipline

- **One WBS item per branch.** Do not start the next item because the current
  one finished early.
- **Do not create a new module** without asking. `WBS.md` names every module
  this project will have.
- **Do not refactor code from a previous, merged item** unless the current item
  cannot be built without it — and then say so explicitly before doing it.
- **No new runtime dependencies** beyond `pyyaml` (W9) and `openai` (W10). Dev
  dependencies are `pytest`, `mypy`, `ruff`, and nothing else.
- Stay within the line budget in `WBS.md`. Being 30% over is a signal that
  something is being built that was not asked for.

## Verification

Before any commit, and before claiming an item is done:

```bash
ruff check . && ruff format --check . && mypy --strict src tests && pytest -q
```

Chained with `&&` deliberately. A green pytest after a red ruff is not a green
build.

**Report what the commands actually printed.** Do not summarise a test run as
"all tests pass" — paste the count. If something fails, report the failure
before fixing it, not only the fixed state.

**Never disable a check to make it pass.** No `# type: ignore`, no `# noqa`, no
`@pytest.mark.skip`, no loosening `mypy` settings. If a check is genuinely
wrong, say why and wait.

## Testing

This is the part of the repo that matters. The subject of the project is *how
you know whether an agent worked*; a harness whose own correctness is unchecked
is not making a point, it is making a claim. Git hygiene here is deliberately
loose. Test rigour is not.

**Every item ships tests in the same commit as its code.** Not "tests to
follow." An item with untested code is not done.

### Cover four things per module

1. **The happy path** — one test, the obvious one.
2. **Every failure mode the module is supposed to detect**, one test each, named
   after the failure. If the evaluator checks five things, five tests.
3. **The boundary** — empty trace, zero attempts, missing terminal event,
   `None` answer, limit hit exactly rather than exceeded.
4. **The distinction the module exists to preserve** — for this repo, almost
   always "judged and failed" vs "never judged." Assert they are not equal, not
   just that each is right.

### Verify tests can fail

For every check that matters: delete the check, run the test, confirm it goes
red, restore it. Then say in the commit message which tests were fail-first
verified. A test that has never been observed failing is an assertion, not
evidence.

Do this for at least three tests per item, and always for the one covering the
item's core distinction.

### Test against constructed inputs, not runs

Build `list[TraceEvent]` by hand. Do not run an agent to produce a trace and
then assert on it — that couples the evaluator's tests to the runner's
behaviour, so a runner bug shows up as an evaluator failure. Hand-built traces
are faster, deterministic, and isolate the failure to one module.

The mock runner (W3) exists for the orchestrator's tests specifically. It is
not a substitute for hand-built traces elsewhere.

### Invariants get asserted as invariants

Where a property must hold universally, assert the identity itself, not an
example of it:

```python
assert s.passed + s.failed + s.not_evaluated == s.total_attempts
```

not three separate equality checks against expected numbers. Check the exact
numbers *too* — but the identity is what catches an implementation that is
internally consistent and wrong.

### Write orchestrator assertions before the implementation

W6 is the one item where the code and its tests can be wrong in the same
direction and still come back green, because the same process wrote both. For
that item, the partition identity and the per-task `k`/`n` expectations are
written first, by hand, and the implementation is made to satisfy them.

### No network, ever

Including W10, which injects a fake client through a constructor seam. A test
that needs a key is not a test.

### Naming

Test names state the claim being made:
`test_failed_attempts_excludes_infrastructure_failures`, not `test_summary_2`.
When a test encodes a judgement that could reasonably go the other way, say so
in a docstring — e.g. that an unevaluated attempt is absent evidence rather
than contrary evidence.

### Coverage

`pytest --cov=minieval` is allowed and `pytest-cov` is an accepted dev
dependency. Treat the number as a smoke alarm, not a score: it finds code no
test touches, and says nothing about whether the tests that exist check the
right thing. Do not chase a target.

## Style

- Python 3.11, `from __future__ import annotations`, full type annotations.
- Dataclasses, not Pydantic. `StrEnum` for closed sets.
- Frozen dataclasses for config and specs; mutable for results.
- **Derive, do not store.** If a value can be computed from another field, make
  it a `@property`. Two fields that can disagree about one fact is the specific
  bug this project is avoiding.
- Docstrings explain *why*, not *what*. The signature already says what. A
  docstring that records a rejected alternative is worth five that restate the
  parameter list.
- Line length 88, `ruff format` decides everything else.

## Reporting results

- **k/n against named items, never percentages.** With n=8 and repeats=3, "67%"
  is noise dressed as a measurement.
- **Report negative results plainly, before any tuning.** A stably failing
  scenario named in the README is worth more than a tuned number.
- **Do not describe cost figures as verified.** They are structurally
  unvalidated until reconciled against a real invoice.
- Hold generalisations loosely until data confirms them. "Every judgement
  outside the tools failed" is the kind of claim that gets falsified by the
  next three runs.

## Git

Deliberately loose. Single branch, `main`, commit directly to it. No feature
branches, no merge commits, no PR flow — this is a solo PoC and that ceremony
buys nothing here.

What still applies:

- Commit at natural stopping points, roughly one per WBS item.
- Short, bullet-style messages, and **name which tests were fail-first
  verified**:

  ```
  - mock runner: happy path and four failure modes
  - failure modes driven by TaskSpec.criteria
  - fail-first verified: step_limit, infra_error, denied_tool
  ```

- Never commit `.env`, `runs/`, `*.egg-info/`, or `.mypy_cache/`.
- Run the verification chain before committing, not after.

## When stuck

Say so and stop. Do not:

- work around a failing type check by weakening the type
- add a field to make a test pass
- implement a simplified version and note the simplification in passing
- keep going past the item's scope to "make it work end to end"

A stopped item with a clear question costs one message. An item that
silently expanded costs a review that finds it three items later.
