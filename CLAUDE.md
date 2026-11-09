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
| Smoke | `$ENV/bin/agentevalops run --config configs/toy_smoke.yaml --output runs/tmp` then `validate-bundle` / `replay` on the same dir (format 0.6, 13 files checked, `Refs: deciding (refs_version 1)`; the run registers a plan that declares nothing, so `gate` says `CANNOT JUDGE`, exit 2) |
| Smoke, seed pack | `$ENV/bin/agentevalops run --config configs/agentic_baseline_mock.yaml --output runs/tmp-pack` then `validate-bundle` / `replay` on the same dir (format 0.6, 13 files checked, `controls.json` and `evidence_report.md` among them; the plan is registered first, 26 of 26 scenarios selected; 26 scenarios pass under the four evaluators with the summary's four category lines, each ending in its pass^k and its 95% CI, a `Pass^k` line with the overall interval, and the report's five outcome groups, repeatability table and pre-registered plan; `mock_trajectory: known_bad` fails every one), then `gate --bundle` on the same dir: `GATE: PASS`, exit 0; on the known-bad bundle `GATE: FAIL` with the three hard gates named, exit 1; on a toy bundle `GATE: CANNOT JUDGE`, exit 2; then `rescore --bundle` on the fresh seed dir: `RESCORE: MATCH`, exit 0, `Refs: deciding (refs_version 1)`, `Files: every recomputed file is byte-identical` (a bundle from an older commit says `CODE_CHANGED`, exit 3, with `Verdicts: reproduce`) |

The `Makefile` targets invoke bare executables and are therefore only safe with
the env already activated. Prefer the absolute-path commands above.

**No `git stash`.** To set work aside, commit it to a local branch, and
delete the branch once its work has landed. On 2026-11-02 a stash and pop
emptied the index while the editor's git integration was active.

**A slice that changes what a verdict means gets an independent review
with mutations before it lands** -- the validator, the gate, signing or
rescore. Reviewers who did not write it read the diff through separate
lenses (conformance to the ADR, one path, read-only and paths, the tests),
and one-line mutations of each new rule must each fail a test; every
confirmed finding is fixed with a test before the commit. In R29 (b2) this
found a forgery that rescored as `MATCH` and ten of twelve mutations
surviving the first tests.

**A mutating reviewer never works on the only copy.** A killed session
never runs a `finally` block, so restoring mutated files in-process is no
safety net: on 2026-11-08 a mutation reviewer stopped mid-run with one line
of `orchestration/local.py` removed, and four tests failed a session later.
Before any mutating reviewer starts, commit the slice to a local WIP branch,
so a leak shows as `git diff` against that snapshot and git can restore it;
and let the reviewer mutate a `git worktree` of the snapshot, not the live
tree. The package is installed editable from the live tree, so a copy's
test run can silently import the live source: print `agentevalops.__file__`
from the copy's run and check it points into the copy before trusting any
result.

**Don't edit files while a gate is running on the tree.** Wait for it to
finish, or stop it first. Tests read the checkout's own git state and
files, so an edit mid-run makes the result describe neither the old tree
nor the new one: on 2026-11-01 a docs-only gate reported 70 failures
because catalogue code was edited while it ran.

**Before any tag:** follow `docs/release-checklist.md`, and run the IP
script against all three pinned PDFs (in `~/Documents/aeo-sources/`, never
committed): `$ENV/bin/python scripts/check_catalogue_ip.py
~/Documents/aeo-sources/mgf-for-agentic-ai.pdf
~/Documents/aeo-sources/starter-kit-llm-testing.pdf
~/Documents/aeo-sources/aivtf-2025.pdf` (add `--extra FILE` with a fresh
seed run's `evidence_report.md` and `report.md` joined into one file:
`--extra` takes one file, and given twice keeps only the last, which
silently skips the evidence report and its identifiers). It must name each PDF's
pinned source and report 0 new overlaps; known ones are on
`scripts/ip_allowlist.yaml`, which admits only identifiers and cited names.
CI cannot run it.

**Every gate or CI command chain starts with `set -o pipefail`.** Without it a
pipeline's exit status is its last command's, so `pytest ... | tail -1` or
`ruff check ... | grep ...` reports success whatever the gate found, and a
`&&` chain behind it carries on. Twice on 2026-11-01 a pipeline masked a
failing exit status this way: once ruff findings were committed and had to be
amended before the push, once a failing test was. With `set -o pipefail` the
pipeline fails when any stage fails. If the output must be trimmed, write it to
a file, keep the status, and tail the file afterwards:

```bash
set -o pipefail
$ENV/bin/python -m pytest -q > /tmp/pytest.txt 2>&1; rc=$?; tail -1 /tmp/pytest.txt
test "$rc" -eq 0 || exit "$rc"
```

(`status` is read-only in zsh; use another name.)

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

## Baseline (verified 2026-11-09, Sai2608, clean checkout at `17a7361`)

- ruff: clean
- mypy: clean (88 source files) — dedicated `anthropic.*` and `openai.*`
  mypy overrides in `pyproject.toml` (mirroring the pre-existing
  `opentelemetry.*` one) keep CI (which installs only `.[dev]`) green
  despite both SDKs being lazily-imported optional deps. Sai2608 has
  `openai` and `opentelemetry-api` installed, so local `mypy src` is more
  permissive than CI's — see "Local env vs CI" above.
- `lint-imports`: **9 kept, 0 broken** (7th contract added alongside
  row 14's SWE-bench spike: "swebench never imported"; 8th with catalogue
  v0: "catalogue imports only core"; 9th with R23: "environments imports
  only core"; the layers contract's low tier is `catalogue | environments`,
  ADR-0006 D8; with R24 slice 4, `domain_packs` joined the middle tier, the
  independence contract and every forbidden contract; with R27 slice 2,
  `plans` joined the middle tier and every forbidden contract -- each edit
  proven against an injected violation first; R28 (a)'s `plans` →
  `catalogue` import is the middle tier reaching the low tier, so no
  contract changed; nor did R29's `replay` → `orchestration`, `registry`,
  `bundles` and `plans` imports, all middle tier; with R38 slice 3, `triage`
  joined the middle tier, the five confined-import contracts' sources and
  the catalogue and environments contracts' forbidden lists -- not the
  adapter-independence contract -- proven against injected violations
  first: 9 kept before the edit, 3 broken after, 9 kept once removed)
- pytest: 2060 passed, 1 skipped (opt-in `@pytest.mark.live`), 1 xfailed (the
  M-prefix look-alike, pending a verified check-letter table; ADR-0006 D26),
  98% branch coverage on a clean checkout (`17a7361`, R42 met). In CI the
  two case-insensitive path tests also skip, on Linux's case-sensitive
  disk. The
  signing tests (`tests/test_signing.py`) need `ssh-keygen` from OpenSSH 8.2
  or newer: where it is missing or older they skip, naming what is
  missing, and with `AGENTEVALOPS_REQUIRE_SSH_KEYGEN=1`, as CI sets it,
  they fail instead. Since 2026-11-09 `ssh-keygen` signs on this machine,
  so they run locally -- the triage walker's signed-bundle byte-identity
  test among them, with a throwaway key in its own temp HOME. They make
  throwaway keys in their own temp dirs and never touch `~/.ssh` or a
  running agent. The
  suite takes about four minutes without coverage and five with it on
  this machine, under two minutes per CI job. A toy, a seed, a
  known-bad and two carried-result bundles are written once per session
  and shared (`tests/conftest.py`, `tests/reference_bundles.py`): a test
  that changes one copies it first, and the session fails if any shared
  file changed. Tests read and write
  YAML through `tests/fast_yaml.py` (libyaml). Since the 0.6 bump the
  coverage figure no longer depends on the checkout's state (`ROADMAP.md`
  §0, the 2026-10-10 finding, closed): a 0.6 bundle's drift check compares
  package hashes, and the 0.5 branch is tested with an injected git probe
  -- a dirty tree measured the same 80 missed lines as the clean one.
  Still measure the baseline only after committing, on a clean tree.
- CI floor: `--cov-fail-under=94`

## CI — watch every push

Run `gh run watch` (or `gh run list --branch main --limit 5`) after every
push, and don't end a session while CI on `main` is red. CI ran red from
2026-09-15 to 2026-10-31 — four pushes, every test job — because a test
passed on macOS by thread timing and nobody looked at the Actions runs
(`ROADMAP.md` §0, 2026-10-31; the W8 entry there is the same pattern). A
local green gate is not CI: CI runs Linux and installs only `.[dev]`.
- Bundle format: `BUNDLE_FORMAT_VERSION = "0.6"` (`bundles/constants.py`;
  0.6 since `3a2d0bc`, ADR-0007, with `"0.1"`-`"0.5"` in the legacy set);
  `tests/fixtures/bundles/seed-narrow-0.5` is a real 0.5 bundle that must
  keep validating; `seed-narrow-0.6` and `seed-narrow-known-bad-0.6` are the
  golden 0.6 bundles rescore is tested against, regenerated only for an
  intended format change (ADR-0003 Q5); `"0.1"` bundles (incl. `runs/toy-smoke*` on disk) still
  validate/read — see `docs/adr/0002-outcome-taxonomy.md`

## README diagrams

The README's two diagrams are generated. To change one, edit the data at
the top of `scripts/render_diagrams.py`, run
`$ENV/bin/python scripts/render_diagrams.py`, and commit the four SVGs it
writes to `docs/diagrams/`. `tests/test_diagrams.py` fails when the
committed SVGs are not a fresh render, and the script itself refuses data
that leaves out or makes up a package, a line too long for its box, or a
claim word.

## Optional extras

- `llm` — `anthropic` (for `agents/langgraph_runner.py`) and `openai` (for
  `agents/openai_runner.py`, docs/adr/0001-openai-runner-chat-completions.md).
  Real runs cost money and need `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  respectively. Never run `configs/toy_langgraph.yaml` or
  `configs/toy_openai.yaml` without being asked to.
- `otel` — scaffold only, emits nothing.

## Keys and `~/.ssh`

Never read, use or print anything in `~/.ssh`, and never use the running
`ssh-agent`: both hold the owner's real keys. A key needed for a test or a
manual check of signing is a throwaway made with `ssh-keygen` in a temp
directory, with `HOME` pointed at that directory; a key with a passphrase is
loaded into a private agent started there (`ssh-agent -a <dir>/agent.sock`),
never the running one. Delete the directory and stop the agent afterwards.
`tests/test_signing.py` already works this way.

## Third-party framework text (IMDA, AI Verify Foundation)

The AI Verify Testing Framework may not be reproduced without written
permission, and this project treats MGF-Agentic and the Starter Kit the same
way until their terms are checked (ADR-0005 D5; the request is `ROADMAP.md`
E1). Never paste a sentence from them into this repository —
docs, catalogue data, fixtures, docstrings, tests or commit messages. Cite
identifiers (process `8.3.2`, MGF-Agentic §2.3.2) and reuse the labels in
`docs/imda-alignment.md` instead of writing new ones from the source. ARC
(GovTech) is Apache-2.0: its IDs and labels may be used with attribution
and a `NOTICE` entry.
