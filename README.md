# MiniEval

MiniEval is a deliberately small, offline-first harness for evaluating
tool-using AI agents. It records a typed trace, evaluates task criteria,
checks policy after execution, repeats tasks to expose instability, and writes
both machine-readable evidence and a decision-ready Markdown report.

This is a learning artifact, not a production evaluation platform. The design
and its deliberate exclusions are documented in [DESIGN.md](DESIGN.md); the
implementation sequence and current status live in [WBS.md](WBS.md).

## What is implemented

- Typed trace events and result schemas
- A deterministic offline mock runner and nine-task toy benchmark
- Deterministic evaluation with trace citations
- Post-hoc policy verdicts kept separate from in-run resource limits
- Repeat runs classified as stable pass, flaky, stable fail, or not evaluated
- A flat on-disk bundle with Git provenance
- A Markdown report with task-level `k/n`, named stability groups, and
  attempt-level findings

The command-line interface and OpenAI-compatible runner are later WBS items and
are not implemented yet.

## Installation

MiniEval uses a standard `src` package layout. Install the checkout in editable
mode before importing it from a regular Python process:

```bash
python -m pip install -e .
```

Python 3.11 or newer is required. The currently implemented offline path has no
runtime dependencies outside the standard library.

## Run the offline benchmark

The current interface is the Python API:

```python
import asyncio
from pathlib import Path

from minieval.benchmarks.toy import toy_tasks
from minieval.bundle import write_bundle
from minieval.evaluate import DeterministicEvaluator
from minieval.orchestrator import run_benchmark
from minieval.runners.mock import MockRunner
from minieval.schemas import PolicySpec, RunConfig, RunId


async def main() -> None:
    run_id = RunId("mock-example")
    config = RunConfig(
        run_id=run_id,
        repeats=3,
        policy=PolicySpec(deny_tool_names=("post_invoice",)),
    )
    summary = await run_benchmark(
        toy_tasks(),
        config,
        lambda current_run_id, attempt: MockRunner(current_run_id, attempt),
        DeterministicEvaluator(),
    )
    bundle_dir = write_bundle(summary, Path("runs"))
    print((bundle_dir / "report.md").read_text())


asyncio.run(main())
```

This run is deterministic and needs neither a network connection nor an API
key. The default step limit intentionally stops T07, T08 simulates an
infrastructure failure, and T09 varies by attempt index to demonstrate flaky
classification.

## Bundle contents

Each run is written beneath `runs/<run_id>/`:

```text
metadata.json       run identity, creation time, and Git provenance
config.json         resolved RunConfig
traces.jsonl        every trace event, self-locating by task and attempt
results.json        structured attempt and task results
report.md           human-readable projection of the RunSummary
replay_command.txt  recorded invocation inputs and provenance
```

`report.md` is generated from the same in-memory `RunSummary` as
`results.json`; it is not an independent result store. It reports named `k/n`
observations rather than percentages, preserves failed versus never-evaluated
attempts as separate counts, and labels reported cost as not invoice-verified.

## Report API

To render without writing a bundle:

```python
from minieval.report import render_report

markdown = render_report(summary)
```

The report contains:

- Run metadata and the exhaustive attempt partition
- Per-task result, evaluated count, stability, and policy-verdict counts
- Named task groups for every stability class
- Evaluation and non-pass policy findings with trace-step citations
- Explicit empty-state output when a run contains no tasks

Task descriptions and identifiers are normalized for safe Markdown table
rendering. The output always ends with a newline and never expresses results as
percentages.

## Verification

The repository's definition of done is:

```bash
ruff check .
ruff format --check .
mypy --strict src tests
pytest -q
```

Tests use constructed inputs at evaluator, policy, report, and bundle
boundaries. No test requires network access.
