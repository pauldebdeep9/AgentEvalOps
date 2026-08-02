"""Tests against checked-in, real (never-regenerated) bundle fixtures.

See ``tests/fixtures/bundles/*/README.md`` for what produced each fixture and
why it must never be regenerated to match current code — a fixture updated to
match current behavior can't catch current behavior regressing, the same
discipline as this project's mutation-testing rule.
"""

from __future__ import annotations

from pathlib import Path

from agentevalops.bundles.validator import validate_bundle

_FIXTURES = Path(__file__).parent / "fixtures" / "bundles"


def test_partial_infra_failure_fixture_fails_validation() -> None:
    """``partial-infra-failure-2026-08-03`` is a genuine, incomplete bundle
    (only ``traces.jsonl``) left behind when an uncaught ``InfrastructureError``
    aborted a live run before any bundle-sealing step ran (see the fixture's
    README.md and ``ROADMAP.md`` §0). ``validate_bundle`` must reject it, and
    must reject it for the *specific* reasons this fixture actually has —
    not merely with a non-zero-equivalent ``valid=False`` — so a future change
    that silently starts treating a half-written bundle as valid is caught
    here instead of surfacing as a corrupted bundle in the wild.
    """
    bundle_path = _FIXTURES / "partial-infra-failure-2026-08-03"

    result = validate_bundle(bundle_path)

    assert result.valid is False
    # traces.jsonl is present (it's the one file LocalTraceStore writes
    # incrementally, independent of the orchestrator ever completing), so it
    # must NOT appear in the missing-file errors -- every other required file
    # must.
    assert result.errors == [
        "Missing required file: metadata.json",
        "Missing required file: config.json",
        "Missing required file: evaluations.json",
        "Missing required file: policy.json",
        "Missing required file: summary.json",
        "Missing required file: report.md",
        "Missing manifest file: manifest.json",
    ]
    assert (bundle_path / "traces.jsonl").exists()


def test_partial_infra_failure_fixture_traces_are_plan_only() -> None:
    """Documents exactly what the fixture contains, so a future edit to it
    (which shouldn't happen -- see the README) is caught immediately: two
    ``agent.plan`` events, no cost or terminal events, because both toy tasks
    failed on the model call before any response was ever parsed.
    """
    import json

    bundle_path = _FIXTURES / "partial-infra-failure-2026-08-03"
    lines = (
        (bundle_path / "traces.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    events = [json.loads(line) for line in lines]

    assert len(events) == 2
    assert all(e["kind"] == "agent.plan" for e in events)
    assert all(e["cost_delta_usd"] == 0.0 for e in events)
    assert all(e["tokens_delta"] == 0 for e in events)
