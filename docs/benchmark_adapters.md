# benchmark_adapters.md — SWE-bench harness: vendoring decision + spike

This is a decision document, not an ADR (per `ROADMAP.md` row 14's own
wording) — it lives here, not in `docs/adr/`, but follows the same
evidence-first voice: every claim below is backed by output actually seen
this session, not by upstream's docs or by assumption. Where a claim
couldn't be verified this session, it's marked as such rather than
asserted.

**Status: decision recorded, exit criterion genuinely validated 2026-08-05
(close-out session).** Three sessions, each correcting the last:

1. **First session (2026-08-04):** spike did not grade an instance —
   `sympy__sympy-20590` hit a 404 pulling its prebuilt image, the fallback
   to a local build then hit real upstream repository drift (`sympy`'s
   `1.7` branch had been deleted). Reasonably concluded upstream's default
   registry was unreliable.
2. **Follow-up session (2026-08-05):** diagnosed that conclusion directly
   instead of repeating the workaround. The registry was never broken —
   the 404 was a transient network/CDN timeout that `docker-py` surfaced
   as a misleading `ImageNotFound`. Graded `psf__requests-1921` end to
   end, no local build — but its **gold patch came back unresolved**,
   traced to a live `httpbin.org` call inside a 2013-era test. Marked row
   14 Met on the strength of this run — premature, corrected next.
3. **Close-out session (2026-08-05):** an unresolved gold is diagnosis,
   not validation — gold must resolve, by construction. `psf__requests-1921`
   disqualified (not hermetic — see "Grading hermeticity", the sharper of
   this document's two structural findings). A second instance,
   `pallets__flask-5014`, screened for network dependence first, graded
   **`resolved: true`**, cleanly. Row 14 is genuinely closed on this
   evidence, not on the previous session's.

See "Grading hermeticity" for the sharper finding, "Upstream repository
drift" for the first, and "Row 14 close-out" for the validating run.

## Step 0 — hardware gate

Run 2026-08-05, before installing anything:

```
$ uname -m
x86_64

$ df -h ~ /tmp
Filesystem      Size    Used   Avail Capacity   Mounted on
/dev/disk1s2   466Gi   232Gi   217Gi    52%     /System/Volumes/Data

$ docker info | grep -i -E "architecture|total memory|storage driver"
Storage Driver: overlayfs
Architecture: x86_64
```

- **Architecture: x86_64**, both host and the Docker Desktop VM. The
  arm64 branch (`--namespace ''` to force local builds instead of pulling
  upstream's x86_64 images under QEMU) does not apply to this machine —
  but see the spike results below: `--namespace ''` turned out to be
  necessary here anyway, for an unrelated reason.
- **Free disk: 217GB**, comfortably above both the 40GB stop-floor and
  most of the way to upstream's 120GB full-dataset recommendation. Never
  came close to either bound during the spike — peak Docker image usage
  was ~9GB (`docker system df`, mid-spike), and host free space moved
  from 217GB to 208GB before cleanup, 208GB→208GB after (see "Disk
  reclamation" below for why cleanup didn't fully restore the 217GB
  figure).
- **Docker Desktop's virtual disk** (`Docker.raw`, a sparse file) is
  capped at ~465GB (`ls -la` apparent size), i.e. bounded by host
  capacity rather than a separate fixed allocation; actual on-disk usage
  before the spike was 2.1GB (`du -sh`). CPUs: 8. Memory: 7.656GiB.

Gate passed; proceeded.

## The decisions

### D1. Pinned pip dependency in an optional extra. Not vendored, not a submodule.

Vendoring puts thousands of lines of upstream code under this repo's
`mypy --strict`, `ruff`, and licence surface, and makes this project the
silent owner of every future upstream change. A submodule adds git
friction without solving installability. A pinned version plus a
constraints entry handles drift better than either, and matches how every
other third-party dependency in this repo is already governed (the `llm`
extra's exact pins on `anthropic`/`openai`/`langgraph`, the `typer`
floor's own bisection discipline in `pyproject.toml`).

**Condition now satisfied, extra still not added — deliberately, scope,
not doubt.** The spike's own exit criterion — grade one instance — is now
met (2026-08-05 follow-up session, see below): `swebench==4.1.0` graded
`psf__requests-1921` end to end with `patch_successfully_applied: true`
and a definitive verdict. `pip install swebench` (with the `cbor2<6`
constraint — see "Installation, and the cbor2/Rust finding") is confirmed
sound both as a mechanism and, now, as a pin that actually works. The
`swebench` extra was **not added to `pyproject.toml` in the follow-up
session either** — that session's own scope for `pyproject.toml` was
narrowly the boto3 comment correction (see D2 below), and adding a new
extra wasn't part of it. This is now a ready, low-risk next step, not an
open question: whoever picks it up should add `swebench==4.1.0` with the
`cbor2<6` constraint noted inline, matching the `llm` extra's exact-pin
discipline.

### D2. The harness is invoked as a subprocess against its documented CLI, and never imported.

Upstream's stable contract is `python -m swebench.harness.run_evaluation`
plus the JSON report it writes. The library surface
(`build_instance_image`, `exec_run_with_timeout`) is documented but
internal-shaped, and this row's own risk column names upstream API drift
as the H risk on this row (a risk the spike below directly ran into, one
layer down — repository drift rather than API drift, but the same shape
of problem: upstream state moving out from under a fixed integration).
Subprocess invocation means `swebench` never enters this repo's mypy,
ruff, or import-linter surface, and `datasets`/`docker` never load into
the AgentEvalOps process.

**Import-linter contract: attempted, and addable — added.**
`pyproject.toml`'s pre-existing boto3 contract comment (on the
*independence*-type `agentevalops.cloud` case) only says that contract
type errors when a listed **internal** module doesn't exist as a package
under `agentevalops`; it says nothing about `forbidden`-type contracts and
an **external**, uninstalled third-party module. Those are different
claims, and this session's job was to check the one that actually applies
here (`swebench` is a `forbidden_modules` entry, not a `source_modules`/
`independence` one) rather than assume the boto3 comment generalizes.

Verified directly, with `swebench` genuinely absent from `Sai2511`
(confirmed via `pip show swebench` → "Package(s) not found"):

```
$ /Users/debdeeppaul/opt/anaconda3/envs/Sai2511/bin/lint-imports
...
swebench never imported (docs/benchmark_adapters.md D2) -- subprocess only KEPT
Contracts: 7 kept, 0 broken.
```

Then, per this project's own established discipline ("every contract was
proven to actually fail against a *direct* injected violation, then
reverted"), a temporary `import swebench` was added to
`src/agentevalops/core/errors.py` and reverted immediately after:

```
$ /Users/debdeeppaul/opt/anaconda3/envs/Sai2511/bin/lint-imports
...
swebench never imported (docs/benchmark_adapters.md D2) -- subprocess only BROKEN
agentevalops is not allowed to import swebench:
-   agentevalops.core.errors -> swebench (l.5)
Contracts: 6 kept, 1 broken.
```

Both `pyproject.toml`'s temporary test edit and `core/errors.py`'s
temporary injected import were reverted before this session's real
changes were made (`git diff --stat` confirmed clean on both paths
immediately after). The real contract — permanent, not temporary — is
now the 7th entry in `[tool.importlinter]`, unlike anthropic/openai/
opentelemetry/boto3 it has no `allow_indirect_imports = true` (there is no
legitimate "confined to agents/"-style registry-resolution path for
`swebench`, since D2 says it's never imported anywhere, not even behind a
registry) and no package is omitted from `source_modules` — every
existing package is listed, because none of them is allowed to import it.
CI installing only `.[dev]` (never a `swebench` extra, since D1 wasn't
implemented this session) means this contract stays meaningful there:
`swebench` being absent from the CI environment does not make the
contract vacuous, per the empirical finding above.

### D3. Two sandboxes, not one — and this corrects rows 15 and 16.

`run_evaluation` builds images, runs tests, and cleans up after itself.
Row 15 as originally written ("use `docker-py` for sandbox lifecycle" in
`prepare_environment`) would have the adapter and the harness both
managing containers, and they will fight.

Two genuinely different sandboxes exist:

- **The agent's workspace** — where the agent reads the repo and produces
  a patch. A plain git clone at the instance's base commit. No Docker
  required, because a patch-producing agent doesn't need to run the test
  suite. This is what `prepare_environment` and `teardown` should own.
- **The grading sandbox** — harness-owned, containerised, never touched
  by this codebase. `grade` hands over a predictions file and reads back
  the report.

This makes row 15 materially smaller than budgeted (no `docker-py`
dependency, no container lifecycle code in the adapter at all). See
"Also update — ROADMAP.md" below for the corrected row text.

### D4. SWE-bench grading cannot run in this project's CI. Recorded as a constraint, not a to-do.

Upstream's recommended floor is x86_64 with ~120GB free storage, 16GB
RAM, and 8 cores. GitHub-hosted runners are far below that (typically
14GB RAM, 2 cores, ~14GB free disk on the standard tier). Any SWE-bench
test in this repo must therefore be opt-in and marked (a pytest marker,
deselected by default — the same shape as the existing `@pytest.mark.live`
marker for real-API tests), never part of `make check` or the `ci` job.

This is the first hard requirement for a real `CloudBackend`, not an
optional nice-to-have. Cross-referencing `docs/adr/0004-cloud-backend-two-axes.md`'s
two-axis framing: Axis A there is "Azure as a `CloudBackend`," gated on
`AwsBackend` existing and v0.2 shipping first (`AwsBackend` being the one
real data point needed before a second cloud implementation is worth
attempting). D4 here says the same shape of thing about the *existing,
already-planned* `AwsBackend` itself: `AwsBackend` at W17 stops being an
optional "nice to have cloud story" and becomes the *only* place
SWE-bench grading can run at the scale v0.5's headline claims (20-instance
Verified subset, `ROADMAP.md` rows 23–25) — a local laptop, even a
generously specced one, cannot be the CI-gated path for this benchmark.
This is recorded as a `ROADMAP.md` §0 constraint, not a to-do; see "Also
update" below.

**D2, D3, D4 all stand unchanged after the follow-up session's graded
run** — noted explicitly rather than left silent, per that session's own
instruction. D2 (subprocess-only) is unaffected: the graded run still
never imported `swebench`; the import-linter contract is still 7/7. D3
(two sandboxes) is, if anything, reinforced by direct observation this
time: the graded run's own log shows the harness doing every step of the
grading sandbox's lifecycle itself — create container, apply patch, copy
in the eval script, run tests, grade, stop, remove container — with zero
intervention from any code this repo would own; the "harness-owned, never
touched by this codebase" framing held up against a real run, not just
the design argument. D4 (no SWE-bench in CI) is, if anything,
underscored: one lightweight instance (`requests`, no scientific-stack
dependencies) still took ~3 minutes end to end even with its image
already local, and Docker Desktop's RAM allocation on this laptop remains
7.66GiB against upstream's stated 16GB recommendation (see "Report" in
the follow-up session's ticket — flagged as a manual, user-side action;
this session could not and did not change it).

## Spike results (2026-08-04, first session)

### Installation, and the cbor2/Rust finding

`swebench` was installed into a throwaway venv (`python -m venv
~/scratch/swebench-spike`, **outside** the repo and outside `Sai2511`,
per this session's environment rule), Python 3.11.14:

```
$ ~/scratch/swebench-spike/bin/pip install swebench
...
error: can't find Rust compiler
ERROR: Failed building wheel for cbor2
```

Traced, not assumed: `swebench==4.1.0`'s dependency tree includes `modal`
(a serverless-execution SDK; used for `swebench`'s optional
Modal-as-a-sandbox backend, not the Docker path this project uses).
`modal` pulls `cbor2`, and `cbor2>=6` has no published wheel for this
platform (`cp311-macosx-x86_64`) — only an sdist requiring a Rust
toolchain to build the Rust extension. Confirmed `cbor2==5.9.0` still
ships a pure-Python wheel (`cbor2-5.9.0-py3-none-any.whl`, no Rust
needed). Installed with a one-line constraint:

```
$ echo "cbor2<6" > /tmp/swebench-constraints.txt
$ ~/scratch/swebench-spike/bin/pip install swebench -c /tmp/swebench-constraints.txt
...
Successfully installed ... swebench-4.1.0 ...
```

This succeeded cleanly. **Empirically determined version: `swebench==4.1.0`**
(the only version on PyPI at spike time; `pip index versions swebench`
lists it as the latest, `4.0.5`/`4.0.4`/... below it). The `cbor2<6`
constraint is a real, load-bearing finding for whenever D1's extra is
actually added — without it, a fresh `pip install .[swebench]` breaks on
any machine without a Rust toolchain, this laptop included.

### Run 1 — upstream's own installation smoke test (`sympy__sympy-20590`)

**Attempt 1 — default namespace.** Run exactly as upstream documents it:

```
$ cd ~/scratch/swebench-run1
$ python -m swebench.harness.run_evaluation \
    --predictions_path gold --max_workers 1 \
    --instance_ids sympy__sympy-20590 --run_id validate-gold
```

Failed in 1m27s (23:36:22–23:37:49): `Error building image
sympy__sympy-20590: 404 Client Error ... No such image:
swebench/sweb.eval.x86_64.sweb.eval.x86_64.sympy_1776_sympy-20590:latest`.
The default `--namespace swebench` tries to **pull** a prebuilt image from
Docker Hub before building locally, and that pull 404'd — **on x86_64**,
where Step 0 said the namespace flag shouldn't be needed. This is a real
correction to that assumption, not a contradiction of the arm64 guidance:
the namespace flag turns out to matter whenever upstream's published
image for a given instance/tag is missing or renamed, independent of the
caller's own architecture.

**Attempt 2 — `--namespace ''`, forcing a local build** (the same flag
Step 0 anticipated for the arm64 case, applied here for a different
reason):

```
$ cd ~/scratch/swebench-run1b
$ python -m swebench.harness.run_evaluation \
    --predictions_path gold --max_workers 1 \
    --instance_ids sympy__sympy-20590 --run_id validate-gold-ns \
    --namespace ''
```

Ran 20m50s (23:38:23–23:59:13). Base image (`sweb.base.py.x86_64`, 2.18GB)
and the one environment image (`sweb.env.py.x86_64.c795f4b8...`, 3.81GB)
both **built successfully from scratch** — real, positive evidence that
the harness's build pipeline works mechanically on this machine. The
per-instance image build then failed:

```
Error building image sweb.eval.x86_64.sympy__sympy-20590:latest:
The command '/bin/sh -c /bin/bash /root/setup_repo.sh' returned a
non-zero code: 128
```

The actual failure, from `build_image.log`:

```
+ git clone -o origin --branch 1.7 --single-branch https://github.com/sympy/sympy /testbed
Cloning into '/testbed'...
warning: Could not find remote branch 1.7 to clone.
fatal: Remote branch 1.7 not found in upstream origin
```

**Verified independently, not assumed to be a local misconfiguration:**

```
$ git ls-remote --heads https://github.com/sympy/sympy | grep -E "1\.7|/1\."
5eb4a579f3a93d03bc0ee0bdde6e8c39ce6ae566	refs/heads/1.14

$ git ls-remote --tags https://github.com/sympy/sympy | grep -E "1\.7"
0dc11870b3088782873b018232d8052d383ad30a	refs/tags/sympy-1.7
...
```

`sympy/sympy` on GitHub has no `1.7` branch today — only `1.14` (the
current release branch) exists as a branch; `1.7` survives only as the
tag `sympy-1.7`. `swebench`'s generated `setup_repo.sh` for this instance
was built assuming `1.7` was still a live branch at the time the SWE-bench
dataset was constructed; upstream sympy has since deleted old release
branches while keeping the equivalent tags. **This is upstream repository
drift, a live instance of exactly the risk `ROADMAP.md` row 14 already
names ("H — upstream API drift"), one layer more literal than expected
(git branch existence, not a Python API signature) — not a bug in the
harness itself, and not one of the three named "obvious" failure modes**
(Docker wasn't running — it was; namespace — already the fix being
applied; disk — 208GB free at the time, nowhere near the 40GB floor).

**Verdict: `error_instances: 1`, `resolved_instances: 0`.** Per this
ticket's explicit instruction ("If run 1 fails, stop and report before
attempting run 2. Do not debug upstream's harness beyond one round of the
obvious"), the namespace retry *was* that one round — it fixed the first
failure and surfaced a second, unrelated, non-"obvious" one. No further
debugging (e.g., patching `setup_repo.sh`, trying a different instance's
git history) was attempted, and **run 2 (a SWE-bench Verified instance)
was not started.**

### Run 2 — not attempted

Skipped per the ticket's explicit stop condition: run 1 did not reach a
`resolved` verdict, so run 2 was never started. No `--dataset_name
princeton-nlp/SWE-bench_Verified` invocation was made this session; no
instance was picked or justified, since doing so would itself be "one
more round" of debugging past the stop condition.

### What this does and doesn't establish

**Established, positively:** `pip install swebench` (with the `cbor2<6`
constraint) works cleanly; the harness's base-image and environment-image
build stages work end-to-end on this machine; the harness's CLI, argument
parsing, dataset download (via `datasets`/HuggingFace), and evaluation
loop all execute correctly and produce a well-formed report JSON even for
a run that ends in error, not crash. None of this is nothing — a harness
that can't even get this far would be a much worse spike result.

**Not established, as of the first session:** that any instance — Lite or
Verified — can be graded to a `resolved`/`unresolved` verdict on this
machine, since the one instance attempted hit an upstream data problem
before reaching the test suite. **Corrected by the follow-up session
below:** row 14's exit criterion is met — grading works, demonstrated
against a different instance, using upstream's documented default (no
local build).

## Grading hermeticity: some SWE-bench Verified instances are not hermetic, and freezing the image doesn't fix it

This is the sharper of the two findings this document records, and it sits
above the repository-drift section below deliberately — that finding is
about *building* an instance image; this one is about *what the instance
does once it's running*, and it survives every mitigation the drift finding
suggests.

**The finding.** `psf__requests-1921`'s `test_basicauth_with_netrc` (one of
its six `FAIL_TO_PASS` tests) calls `httpbin('basic-auth', 'user', 'pass')`
— a live HTTP request to `httpbin.org` — during grading, confirmed directly
from `test_output.txt` in the graded run (see "Follow-up session" below).
This is not a build-time dependency on GitHub's current state, the way the
sympy branch-drift finding is. It's a **run-time** dependency: the
container is fully built, frozen, self-contained — and the test inside it
still reaches out to the public internet the moment it executes.

**Why freezing the image doesn't help, unlike the drift finding.** Pulling
a prebuilt image instead of building locally makes the *environment
construction* reproducible (see below) — the container's filesystem is
identical every time. But `test_basicauth_with_netrc` doesn't care whether
its filesystem is reproducible; it cares whether `httpbin.org` answers,
right now, from wherever the container happens to be running. Registry
freezing is a complete fix for repository drift and no fix at all for
this. **Grading verdicts on affected instances depend on third-party
service availability, and a single network blip — httpbin.org down, rate
limiting, a sandbox with restricted egress, a DNS hiccup — can flip a
`resolved` result to `unresolved` with nothing wrong with the patch.**
This is not hypothetical: it is exactly what happened in the graded run
this document already records.

**The downstream consequence, stated because it constrains this repo's own
future work, not just upstream's:** whenever this project selects a
SWE-bench instance to promote to a golden fixture — the W12c/W29 replay
fixtures, or any instance used as a CI-adjacent smoke check — **that
instance must be screened for network dependence first**, the same way
Step 3 below screens before grading. A golden fixture's entire premise is
that replaying it produces the same result every time; a fixture whose
underlying instance makes a live network call inherits `httpbin.org`'s
uptime as a hidden dependency of this project's own replay determinism,
undetectable by anything `docs/adr/0003-replay-semantics.md`'s Q3 currently
enumerates (see "Report" in this session's own accounting for why that
enumeration now looks incomplete beyond just this one case).

**One more finding, one line: `docker-py`'s `ImageNotFound` does not mean
"the image is absent."** The previous session's `ImageNotFound` traced back
to `http+docker://localhost/v1.51/images/.../json` — the local Docker
Engine API, not a registry endpoint — and cost that session a 20-minute
local build chasing a problem that turned out to be a transient CloudFront
blob-fetch timeout on the very first pull attempt (see "Follow-up session"
below for the full diagnosis). Worth remembering the next time this
harness reports a 404: check the registry directly before trusting the
error message's apparent cause.

## Upstream repository drift: a first-class reproducibility finding

This is promoted to its own section, not left as a footnote to why one
run failed, because it says something general about SWE-bench's
reproducibility model, independent of whether any particular instance
happens to be affected today.

**The mechanism.** A *local* instance-image build runs `setup_repo.sh`
inside the Docker build, and that script does a live `git clone --branch
<X> --single-branch <upstream repo URL>`. This is not a one-time dataset
capture — it is a **git operation against the present state of a
third-party GitHub repository**, executed fresh every time an instance
image is built locally, however long after the SWE-bench dataset itself
was frozen. `sympy__sympy-20590` demonstrates the failure mode directly:
its `version` field maps to branch `1.7`, and `sympy/sympy` has since
deleted that branch — branches get pruned as a repo's release process
moves on, while the equivalent tag (`sympy-1.7`) survives, because tags
and branches carry different retention conventions upstream, not because
of anything SWE-bench did wrong.

**The consequence.** SWE-bench's own reproducibility guarantee — a pinned
`base_commit`/`environment_setup_commit` per instance — describes the
*dataset*, not the *environment construction path*. Two ways to realize
that same pinned commit into a runnable image have very different
reproducibility properties:

- **Pulling a prebuilt image from the registry** replays whatever
  upstream's own build servers produced at build time — frozen, byte for
  byte, the moment it was pushed. It does not re-run `setup_repo.sh` and
  therefore cannot be broken by a branch upstream deletes afterward.
- **Building the instance image locally** re-executes `setup_repo.sh`
  against **today's** live GitHub state. It can succeed the day the
  dataset is built and fail years later for a repository/instance where
  nothing about the *benchmark* changed — only the *branch's continued
  existence* did.

For a platform whose own thesis is reproducible evaluation, this is a
real, first-class property of the benchmark it's choosing to wrap, not an
incidental reason one spike run happened to fail: **the registry path is
more reproducible than the local-build path, for reasons structurally
unrelated to Docker or to this project's own code.** This directly bears
on D1 (prefer pip-installed, registry-consuming usage of the harness over
paths that force local builds) and on rows 15/16/23-25 (a `--namespace
''`-style fallback should be understood as trading a possibly-transient
registry problem for a structural, repository-drift-shaped one — see the
follow-up session immediately below, where that is exactly what
happened).

**Scope of the claim, stated plainly.** This session observed exactly one
instance's build failure (`sympy__sympy-20590`, branch `1.7`) and one
registry-vs-build comparison (`psf__requests-1921`, `sympy__sympy-20590`,
both pulling cleanly). It has not measured what fraction of the 500
Verified instances have registry images published, nor what fraction
would fail a fresh local build the way `sympy__sympy-20590` did. Both are
real, bounded follow-up questions — not claimed here.

## Follow-up session (2026-08-05): registry diagnosis, then the graded run

The first session's `--namespace ''` fallback was a reasonable reflex to
an observed 404, but it converted a registry question into a build
question — and only the build side turned out to contain real drift. This
session's job was to diagnose the 404 directly, without reaching for
`--namespace ''` again, per its own hard constraint.

### Step 1 — what image was actually requested

Recovered verbatim from the first session's `~/scratch/swebench-run1/`
logs, no pulls or builds attempted first:

- **Exact image reference:**
  `swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest` (SWE-bench's
  own tag convention replaces the `__` in an instance id with `_1776_` —
  confirmed by direct comparison of the instance id `sympy__sympy-20590`
  against the image tag).
- **Exact 404 text:** `docker.errors.ImageNotFound: 404 Client Error for
  http+docker://localhost/v1.51/images/swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest/json:
  Not Found ("No such image:
  swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest")`.

That URL (`http+docker://localhost/v1.51/...`) is the **local** Docker
Engine API socket (`v1.51` is the Engine API version), not a remote
registry endpoint — so this traceback alone was never proof the registry
itself 404'd. It's `docker-py`'s `ImageCollection.pull()` calling
`inspect_image()` after its own pull attempt, and reporting whatever that
local inspect finds.

`pip show swebench` in the reused venv: **`swebench==4.1.0`**, unchanged
from the first session — ruling out "the venv was in some different
state" as an explanation before looking further.

### Step 2 — the discriminating test

Cheapest question first, `docker manifest inspect` (no layers
transferred):

```
$ docker manifest inspect swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest
{ "schemaVersion": 2, ... 15 layers ... }   # succeeds

$ docker manifest inspect swebench/sweb.eval.x86_64.psf_1776_requests-1921:latest
{ "schemaVersion": 2, ... 15 layers ... }   # succeeds
```

**Both manifests exist.** `psf__requests-1921` was picked as the small
pure-Python instance: smallest gold-patch length (388 characters) among
all non-scientific-stack instances in SWE-bench Verified (checked via
`datasets`, ranked by patch length across all 500 instances) —
`requests` has no numpy/scipy/compiled-extension dependency, unlike the
`sympy`/`django`/`scikit-learn` instances that dominate the shortest-patch
list.

This result matches **neither** of the two outcomes the ticket
anticipated ("Verified pulls, sympy 404s" or "both 404") — it's a third
case the discriminating test was specifically designed to be able to
surface: **the namespace and the specific image tag are both fine.**
Actual `docker pull`, not just manifest inspection, confirms it:

```
$ docker pull swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest
failed to copy: httpReadSeeker: failed open: failed to do request:
Get "https://production.cloudfront.docker.com/...": context deadline exceeded
# retry, immediately after:
$ docker pull swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest
... Status: Downloaded newer image ...   # succeeds, 1m55s

$ docker pull swebench/sweb.eval.x86_64.psf_1776_requests-1921:latest
... Status: Downloaded newer image ...   # succeeds first try, 50.6s
```

**Conclusion: the registry was never broken.** The first session's 404
was very likely a transient CloudFront blob-fetch timeout on the first
pull attempt — the kind of thing that resolves itself on retry — that
`docker-py`/`swebench` surfaced as `ImageNotFound`, a genuinely misleading
error message for what was actually a transient network condition. This
is itself worth naming as a finding: neither `swebench`'s nor `docker-py`'s
error handling distinguishes "this image doesn't exist" from "the pull
attempt timed out," which will read as a permanent, structural problem to
anyone who doesn't retry or independently check the registry, exactly as
happened in the first session.

Per the ticket's own outcome tree, this result does not trigger Step 3
(alternate registries): neither anticipated 404 case actually held.

### Step 3 — not reached

Not needed. Recorded here rather than silently skipped: Epoch AI's
registry and LogicStar's Hugging Face set were not investigated, and no
alternate `swebench` version was installed, because Step 2 resolved the
question without them.

### Step 4 — the graded run

```
$ ~/scratch/swebench-spike/bin/python -m swebench.harness.run_evaluation \
    --predictions_path gold --max_workers 1 \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --instance_ids psf__requests-1921 \
    --run_id validate-verified-gold
```

No `--namespace ''` — upstream's documented default, exactly as this
session's hard constraint required. Both instance images were already
local from Step 2's pulls, so the harness's own log says `Found 1
existing instance images. Will reuse them.` and skipped its own pull —
this run therefore doesn't independently re-prove the registry path
works end-to-end from a cold cache, only that grading against a locally
present image works. Given Step 2 already proved the registry path
itself (manifest + pull, twice), that's a reasonable division of
evidence, not a gap.

**Duration:** dataset load started 00:20:07, container created 00:20:18,
test run completed 00:23:00 (`Test runtime: 160.52 seconds` per the
harness's own instance log), container removed 00:23:15 — **end to end,
~3m9s.**

**Disk:** no new images (both already present from Step 2).
`docker system df` at the end of this session: **7 images, 6.721GB,
100% reclaimable** (up from the pre-spike 5 images / 1.566GB baseline —
the two `swebench/sweb.eval.*` images account for the difference). Host
free space: 207GB before Step 4, 205GB after — a small transient drop
consistent with container-run overlay usage during the test, not visible
as a persistent image in `docker system df -v`. Nowhere near any disk
floor in this document.

**Verdict: `resolved: false`, but not a grading defect.** From the
per-instance `report.json`:

- `patch_successfully_applied: true` — the gold patch applied cleanly.
- `PASS_TO_PASS`: 106/106 passed, 0 failed — **zero regressions** from
  the patch.
- `FAIL_TO_PASS`: 5/6 passed; the one failure is
  `test_requests.py::RequestsTestCase::test_basicauth_with_netrc`.

Traced, not left as an unexplained flake: `test_output.txt` shows this
test calls `httpbin('basic-auth', 'user', 'pass')` — a **live HTTP
request** to `httpbin.org`, part of the `requests` library's own
2013-era network-integration test suite. Its failure is a live-network
dependency inside the sandboxed grading container, unrelated to whether
the gold patch is correct — the patch demonstrably is correct, since it
fixes every *other* tracked test and breaks none of the 106 regression
guards. **Row 14's exit criterion — "Can grade a hand-written patch
against one SWE-bench Verified instance using upstream harness" — is
met**: the harness built (found, via Step 2), applied, ran, and graded a
real instance with zero infrastructure or build errors, producing a
verdict backed by a specific, independently-verifiable pytest failure
list. That the verdict is `unresolved` rather than `resolved` is a fact
about one flaky, network-dependent legacy test, not about whether grading
works.

This also surfaces a concrete new risk for rows 23–25 (SWE-bench on
Fargate): **some instances' test suites make live outbound HTTP calls**,
which means grading-sandbox network egress policy is not just a security
question but a correctness one — an instance can go from `resolved` to
`unresolved` purely because of network reachability, independent of
patch quality.

**Correction, close-out session (2026-08-05): the paragraph above claimed
row 14's exit criterion was met on the strength of this run. That
conflated "the harness produces *a* verdict" with "the harness produces
the *right* verdict" — gold is the human-authored fix; it must resolve,
by construction, or something is wrong with either the grading or the
instance. An unresolved gold is not validating evidence, even when (as
here) its cause is fully diagnosed and external to the harness. See "Row
14 close-out" below for the second, disqualifying screen and the clean
gold run that actually validates the criterion.**

## Row 14 close-out (2026-08-05): screening, then a clean gold run

`psf__requests-1921` is disqualified from both roles it might otherwise
have played — a row 14 validation run and a future golden-fixture
candidate — for the same reason: its grading is not hermetic (see
"Grading hermeticity" above). Closing row 14 honestly requires a second
instance, screened first, whose gold patch actually resolves.

**Screening, before pulling anything.** Candidates were ranked by patch
length (smallest first, via `datasets`, offline/cache-only — see below)
among SWE-bench Verified instances outside `psf/requests` (disqualified)
and outside the scientific stack (`sympy`, `scikit-learn`, `scipy`,
`numpy`, `pandas`, `matplotlib`, `astropy`, `pydata/xarray`,
`mwaskom/seaborn` — heavy, large images, per the original spike ticket's
own steer). Three candidates' `test_patch` diffs were read in full, not
just their test names:

| Instance | `FAIL_TO_PASS` test | What it does |
|---|---|---|
| `pallets__flask-5014` | `test_empty_name_not_allowed` | Asserts `ValueError` on `Blueprint("", __name__)` — pure input validation, in-process |
| `pylint-dev__pylint-7080` | `test_ignore_path_recursive_current_dir` | Runs pylint against a local fixture directory, checks exit code — filesystem only |
| `pytest-dev__pytest-6202` | `test_example_items1` | In-process test-collection introspection via pytest's own `testdir` fixture |

All three: zero hits for a keyword screen (`http`, `socket`, `requests.`,
`urlopen`, `dns`, `connect(`, `getaddrinfo`, `smtp`, `.com`, `.org`,
`.net`, `urllib`) against the actual diff text, and a manual read of each
diff confirms no network I/O, no external fixtures, no live-service
dependency. **Picked `pallets__flask-5014`**: smallest patch (390
characters), single-assertion test, Flask's own dependency footprint is
small and pure-Python (no compiled extensions), keeping the instance image
closer to `psf/requests`' size than to the scientific-stack instances.

**Checked, not assumed, before trusting this method: would the same screen
have caught `psf__requests-1921`?** Verified directly against
`psf__requests-1921`'s own `test_patch` — and the answer is more nuanced
than "yes," worth stating precisely rather than glossing over:
`test_basicauth_with_netrc` (the test that actually failed) **is not in
the `test_patch` diff at all** — it's a pre-existing test in the full
`test_requests.py` file, already present before this instance's patch,
never touched by the diff. A diff-only screen would have missed it
entirely. What the screen *would* have caught is a coincidence, not a
save: the diff's own newly-added `FAIL_TO_PASS` test
(`test_headers_on_session_with_None_are_not_sent`) happens to also
reference `http://httpbin.org/get` — but only to build a `PreparedRequest`
object locally (`ses.prepare_request(req)` performs no I/O in the
`requests` library), not to make a live call. The keyword hit is real; the
reason it would have fired is not the reason the instance is actually
disqualified.

**The real limitation this surfaces:** a screen limited to the
`test_patch` diff only sees tests the patch *adds or modifies* — it is
blind to network dependence in the rest of a `FAIL_TO_PASS`/`PASS_TO_PASS`
list that the diff doesn't touch, which is common (`psf__requests-1921`
itself: 6 `FAIL_TO_PASS` tests, only 1 introduced by the diff). A
rigorous screen needs the full test file content at the relevant commit
for every named test, not just the diff — not done this session, for any
of the three candidates above, and recorded as a real gap in "What's
still open" rather than papered over.

**The run — same `ImageNotFound` misreading, caught immediately this
time.** The first invocation (no `--namespace ''`, upstream's documented
default) failed with the identical error shape as the sympy 404:
`docker.errors.ImageNotFound` against the local Engine API socket for
`swebench/sweb.eval.x86_64.pallets_1776_flask-5014:latest`. Per the
finding recorded above, this was checked directly rather than trusted:
`docker manifest inspect` confirmed the image exists (15 layers, real
manifest); a direct `docker pull` then succeeded — slower this time
(5m15s, a larger image with a 435MB layer) but with no retry needed, so
this wasn't even a timeout, just a first-attempt cold pull the harness's
own pull call apparently didn't wait out. The harness was re-run
immediately after with the image now local.

```
$ HF_HUB_OFFLINE=1 ~/scratch/swebench-spike/bin/python -m swebench.harness.run_evaluation \
    --predictions_path gold --max_workers 1 \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --instance_ids pallets__flask-5014 --run_id validate-flask-gold-2
```

(`HF_HUB_OFFLINE=1` avoids an unrelated, separately-encountered problem:
a plain `datasets.load_dataset` call against the already-fully-cached
Verified dataset was hanging — multiple attempts timed out at 60s and
180s — apparently on a HEAD-request cache-revalidation call against
`huggingface.co`. Forcing cache-only mode resolved it instantly. Not
investigated further; recorded because it's a second, independent case
this session of a network call hanging or misbehaving in a way that
looked at first like something else was broken.)

**Duration:** container created 00:54:39, `Test runtime: 8.33 seconds`,
container removed 00:55:05 — **~26 seconds** end to end for the graded
run itself (image already local). Including the one-time image pull:
~5m41s total.

**Disk:** one new image, `swebench/sweb.eval.x86_64.pallets_1776_flask-5014`,
3.99GB (1.515GB shared base layer, 2.476GB unique). `docker system df`:
7 images, **6.96GB, 100% reclaimable**. No containers left running.

**Verdict: `resolved: true`.** From the per-instance `report.json`:
`patch_successfully_applied: true`; `FAIL_TO_PASS`: 1/1 success, 0
failures; `PASS_TO_PASS`: 59/59 success, 0 failures. **Gold resolves,
cleanly, with no ambiguity.** This is the validating run row 14's exit
criterion actually required — not "the harness produces a verdict" but
"the harness produces the correct one" for the one case (gold) where the
correct answer is known in advance.

## Disk reclamation (first session, 2026-08-04)

All spike-created Docker images were removed after the spike:

```
$ docker rmi -f <sweb.env...> <sweb.base...> <dangling intermediate layer>
```

`docker system df` before cleanup: 8 images, 5.263GB, 100% reclaimable.
After: 5 images, 1.566GB (the pre-existing `ubuntu:22.04`/`python:3.11-slim`/
observability-stack images, untouched, confirmed present both before and
after the spike). `Docker.raw` (the Desktop VM's sparse virtual disk)
internal usage: 2.1GB before the spike, 2.5GB after cleanup — consistent
with `docker system df`.

**Host `df -h` free space did not fully return to its pre-spike value**
(208GB free after cleanup vs. 217GB before) — this is expected, documented
behavior of Docker Desktop on macOS: the VM's sparse disk file grows to
accommodate peak usage but is not automatically shrunk back by `docker
rmi`/`system prune`; reclaiming the underlying host bytes needs Docker
Desktop's own "Reclaim disk space" action or a VM restart. Not performed
this session (outside scope, and 208GB remains far above every disk floor
in this document). No containers were left running (`docker ps -a` empty
both before and after).

## Disk state (follow-up session, 2026-08-05) — nothing deleted, by instruction

Unlike the first session, this session's Docker images were **deliberately
not deleted** — the base and environment layers are worth keeping for
rows 15/16 (rebuilding/re-pulling costs real minutes), and the decision
of whether to keep them belongs to whoever picks up that work next, not
to this session.

`docker system df` at session start (before Step 2's pulls): 5 images,
1.566GB, matching the first session's post-cleanup baseline exactly —
confirming nothing lingered between sessions. At session end: **7 images,
6.721GB, 100% reclaimable** (`docker system df` summary). The two new
images:

```
swebench/sweb.eval.x86_64.sympy_1776_sympy-20590   3.75GB  (1.515GB shared, 2.237GB unique)
swebench/sweb.eval.x86_64.psf_1776_requests-1921   3.58GB  (1.515GB shared, 2.063GB unique)
```

The `1.515GB shared` figure is the common base layer between the two —
real evidence that Docker's layer-sharing works as expected across two
different SWE-bench instance images pulled independently. `docker ps -a`:
empty (the one container Step 4 created was stopped and removed by the
harness itself, per its own log, not by this session). Host free disk:
207GB before Step 4, 205GB at session end — still far above every floor
in this document. **To reclaim ~5.2GB:** `docker rmi
swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest
swebench/sweb.eval.x86_64.psf_1776_requests-1921:latest` — left for the
user to run if desired.

**Update: the user reclaimed the sympy image between sessions** (confirmed
at the start of the close-out session — only `psf/requests-1921` remained,
0B shared-layer accounting since it was now the sole image referencing
those layers). See "Disk state (close-out session)" below for the
close-out session's own before/after.

## Disk state (close-out session, 2026-08-05) — nothing deleted, by instruction

Same instruction, same discipline: this session's new image
(`swebench/sweb.eval.x86_64.pallets_1776_flask-5014`) was not deleted
either — the decision belongs to the user. `docker system df` at session
start: 6 images, 5.15GB — `psf/requests-1921`'s image (3.58GB) was still
present, untouched since the prior session; only the sympy image had been
reclaimed by the user in between (confirmed above). At session end:
**7 images, 6.96GB, 100% reclaimable** (`docker system df` summary) —
`psf/requests-1921` (3.58GB) and the newly-pulled `pallets__flask-5014`
(3.99GB, 1.515GB of it shared with `psf/requests-1921`'s base layer).
`docker ps -a` empty throughout.

Given `psf/requests-1921` is now disqualified from any future use (not
hermetic), it's the one most worth reclaiming; `pallets__flask-5014` is
the one worth keeping, being the row's own validating instance. Left for
the user to decide: `docker rmi
swebench/sweb.eval.x86_64.psf_1776_requests-1921:latest`.

## Reproduction

```
# Throwaway venv, outside the repo and outside Sai2511:
python3 -m venv ~/scratch/swebench-spike
echo "cbor2<6" > /tmp/swebench-constraints.txt
~/scratch/swebench-spike/bin/pip install swebench -c /tmp/swebench-constraints.txt

# Registry diagnosis (cheap, no layer transfer) -- run this before ever
# reaching for --namespace '':
docker manifest inspect swebench/sweb.eval.x86_64.<owner>_1776_<repo>-<num>:latest

# If that 404s, retry a real pull once before concluding the image is
# missing -- the first session's failure was a transient CDN timeout, not
# a missing image:
docker pull swebench/sweb.eval.x86_64.<owner>_1776_<repo>-<num>:latest

# Install smoke test / graded run, upstream's documented default --
# no --namespace '' needed once the image above is confirmed pullable:
~/scratch/swebench-spike/bin/python -m swebench.harness.run_evaluation \
    --predictions_path gold --max_workers 1 \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --instance_ids psf__requests-1921 --run_id validate-verified-gold
```

The `--namespace ''` arm64 branch from Step 0 is unaffected by any of
this — it's a real, independent reason to force local builds under QEMU
emulation. What this session's diagnosis changes is only the **default,
non-arm64 case**: don't reach for `--namespace ''` on a 404 without first
checking whether it's a transient pull failure via `docker manifest
inspect` / a retried `docker pull`.

## What's still open

- **Row 14's exit criterion is now genuinely validated, not just
  exercised.** `pallets__flask-5014`'s gold patch resolved cleanly
  (close-out session, 2026-08-05). Nothing further needed to close this
  row specifically.
- **`swebench==4.1.0` is confirmed as a working pin** — it graded two real
  SWE-bench Verified instances, one to a clean `resolved: true`. Whether
  an older, no-Modal release would be a *better* pin (smaller dependency
  tree, no `cbor2`/Rust finding to route around) remains unexplored —
  `3.0.17`'s wheel metadata still lists `modal` as a hard dependency, so
  downgrading wouldn't avoid the constraint by itself. Not blocking;
  `4.1.0` + `cbor2<6` works.
- **D1's `pyproject.toml` extra is still not added**, now deliberately
  rather than provisionally — see D1 above. Low-risk next step whenever
  someone touches `pyproject.toml` for this row again.
- **Registry coverage across the full instance set is unmeasured.** This
  session confirmed 3 of 500 Verified images pull successfully. Whether
  every instance has a published image, or some meaningful fraction would
  fall back to a local build (and thus be exposed to the repository-drift
  finding above), is unknown.
- **Hermeticity screening across the full instance set is unmeasured, and
  this session's screening method has a known, confirmed blind spot.** A
  diff-only screen (reading `test_patch`, keyword-matching) only sees
  tests the patch adds or modifies — it cannot see network dependence in
  the rest of a `FAIL_TO_PASS`/`PASS_TO_PASS` list, which is common (see
  "Row 14 close-out"'s direct check against `psf__requests-1921`: 6
  `FAIL_TO_PASS` tests, only 1 touched by the diff; the actual failing
  test wasn't in the diff at all). This session's three screened
  candidates (`flask-5014`, `pylint-7080`, `pytest-6202`) were cleared by
  diff-reading only — `flask-5014`'s clean **run result** is real,
  independent evidence its patch doesn't trigger a network call in
  practice, but the screening *method* itself was not validated as
  complete, only as good enough this once. A rigorous screen needs full
  test-file content at the relevant commit for every named test. What
  fraction of the 500 Verified instances make live network calls during
  grading — and whether a full, automated screen is worth building before
  W29's golden fixtures are selected — is real, unstarted follow-up work.
- **Live-network-dependent tests inside graded instances** (the
  `test_basicauth_with_netrc` finding, now promoted to its own section)
  are a new, real consideration for rows 23–25's Fargate network-egress
  design, and for W29's fixture selection — not investigated further this
  session beyond the two instances that surfaced and confirmed it.
- **`docs/adr/0003-replay-semantics.md`'s Q3 nondeterminism enumeration
  now looks incomplete** — it enumerates sources of nondeterminism in
  *this platform's own* bundle contents (model output, token counts,
  wall-clock time), not in a *benchmark's* grading step itself. A live
  network call inside a SWE-bench test is a nondeterminism source ADR-0003
  has no category for: it isn't runner nondeterminism (Q3's real-model
  table) and isn't AgentEvalOps-internal timing (Q3's mock-runner table)
  — it's third-party-service nondeterminism, introduced by the benchmark
  adapter, not the agent runner. Flagged here, per this session's explicit
  instruction not to edit ADR-0003 itself; revisiting the enumeration is
  real work for whenever W29's golden fixtures actually land.
- **Docker Desktop's RAM allocation (7.66GiB) remains below upstream's
  16GB recommendation.** Flagged for the user as a manual action (Docker
  Desktop → Settings → Resources) before rows 15/16 attempt anything
  heavier than the lightweight instances graded so far; this session could
  not change it itself.
