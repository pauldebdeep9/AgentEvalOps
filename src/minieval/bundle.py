"""The run bundle: six flat files under ``root/<run_id>/`` plus the git
provenance they share. No manifest, no checksums, no format version, no
legacy-version table, no validator — DESIGN.md §5 costs that family at
~1,380 lines in the source repo this project is reduced from and cuts
every line of it. ``report.md`` is a human projection of the same
``RunSummary`` written to ``results.json``, not another persisted result model.

git is invoked via ``subprocess``, no new dependency. Each provenance
field is independently the literal ``"unavailable"`` — never ``None``,
never ``""``, never guessed — if its own git call fails, times out, or
the checkout does not exist. ``git_provenance`` returns ``dict[str,
str]``: ``dirty`` is ``"true"``/``"false"`` as a string, not a ``bool``,
deliberately, so nothing here special-cases one field when serialising.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minieval.report import render_report
from minieval.schemas import Attempt, RunSummary

_UNAVAILABLE = "unavailable"
_GIT_TIMEOUT_SECONDS = 2.0
"""Short enough that a hung or unreachable git cannot stall a run."""


def _git(args: list[str], cwd: Path | None) -> tuple[bool, str]:
    """Run one git command. Returns ``(True, stdout)`` on a clean exit,
    stdout stripped; ``(False, "")`` if git could not be run at all
    (missing binary, timeout) or exited non-zero (not a checkout, no
    commits yet, and similar)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, result.stdout.strip()


def git_provenance(cwd: Path | None = None) -> dict[str, str]:
    """Return ``sha``, ``branch``, and ``dirty`` for the checkout at
    ``cwd`` (the current working directory if ``None``). ``dirty`` counts
    untracked files along with modified and staged ones — the question is
    whether the tree exactly matches ``sha``, not just what is staged."""
    sha_ok, sha = _git(["rev-parse", "HEAD"], cwd)
    branch_ok, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    status_ok, status = _git(["status", "--porcelain"], cwd)

    return {
        "sha": sha if sha_ok and sha else _UNAVAILABLE,
        "branch": branch if branch_ok and branch else _UNAVAILABLE,
        "dirty": ("true" if status else "false") if status_ok else _UNAVAILABLE,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _attempt_payload(a: Attempt) -> dict[str, Any]:
    return {
        "index": a.index,
        "output": a.output.to_payload(),
        "evaluation": dataclasses.asdict(a.evaluation),
        "policy": {**dataclasses.asdict(a.policy), "verdict": a.policy.verdict.value},
    }


def _results_payload(summary: RunSummary) -> dict[str, Any]:
    return {
        "total_attempts": summary.total_attempts,
        "passed_attempts": summary.passed_attempts,
        "failed_attempts": summary.failed_attempts,
        "not_evaluated_attempts": summary.not_evaluated_attempts,
        "total_cost_usd": summary.total_cost_usd,
        "tasks": [
            {
                "task_id": str(t.task.task_id),
                "k": t.k,
                "n": t.n,
                "evaluated": t.evaluated,
                "stability": t.stability.value,
                "attempts": [_attempt_payload(a) for a in t.attempts],
            }
            for t in summary.task_results
        ],
    }


def write_bundle(summary: RunSummary, root: Path) -> Path:
    """Write a run bundle to ``root/<run_id>/`` and return that directory.
    Overwrites an existing directory's files in place -- no failure, no
    append, no suffixed variant; re-running a run_id means "this is that
    run's record now."

    ``results.json`` repeats k/n/evaluated/stability and the partition
    counts, and every ``traces.jsonl`` line repeats its own run_id/
    task_id/attempt, even though each is derivable -- a deliberate
    departure from this project's derive-do-not-store rule everywhere
    else. A bundle is a record for a reader with no code: recomputing a
    ``@property``, or reconstructing which attempt a line belongs to from
    file order, is not available to someone reading one file in
    isolation.
    """
    bundle_dir = root / str(summary.run_id)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    config = summary.config
    git = git_provenance()

    _write_json(
        bundle_dir / "metadata.json",
        {
            "run_id": str(summary.run_id),
            "created_at": datetime.now(tz=UTC).isoformat(),
            "git": git,
        },
    )
    _write_json(bundle_dir / "config.json", dataclasses.asdict(config))
    (bundle_dir / "traces.jsonl").write_text(
        "".join(
            f"{event.to_json()}\n"
            for task_result in summary.task_results
            for attempt in task_result.attempts
            for event in attempt.events
        )
    )
    _write_json(bundle_dir / "results.json", _results_payload(summary))
    (bundle_dir / "report.md").write_text(render_report(summary))
    (bundle_dir / "replay_command.txt").write_text(
        "minieval run record -- not a runnable command\n"
        f"run_id:       {config.run_id}\n"
        f"runner:       {config.runner}\n"
        f"model:        {config.model or 'none'}\n"
        f"repeats:      {config.repeats}\n"
        f"max_steps:    {config.limits.max_steps}\n"
        f"max_cost_usd: {config.limits.max_cost_usd}\n"
        f"policy_id:    {config.policy.policy_id}\n"
        f"git_sha:      {git['sha']}\n"
        f"git_branch:   {git['branch']}\n"
        f"git_dirty:    {git['dirty']}\n"
    )

    return bundle_dir
