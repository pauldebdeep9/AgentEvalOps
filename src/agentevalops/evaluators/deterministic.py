"""DeterministicEvaluator — scores AgentOutput without calling any model."""

from __future__ import annotations

from agentevalops.core.schemas import (
    AgentOutput,
    EvaluationResult,
    EvaluatorContext,
    Ref,
    TaskSpec,
    TerminationReason,
    ToolCallRecord,
    TraceEvent,
    TraceEventKind,
)


def _scoped_records(context: EvaluatorContext | None) -> list[ToolCallRecord]:
    """This task run's harness records, in call order; none without a
    context."""
    if context is None:
        return []
    records = context.records
    if context.task_id is not None:
        records = tuple(
            r
            for r in records
            if r.task_id == context.task_id
            and (context.repetition is None or r.repetition == context.repetition)
        )
    return sorted(records, key=lambda r: r.index)


class DeterministicEvaluator:
    """Scores a completed task using local, deterministic criteria only.

    All checks are derived from ``task.metadata``.  No model, no network,
    no external I/O.  All checks are AND-combined: any single failure causes
    the overall result to be ``passed=False``.

    Checks applied
    --------------
    1. **Completion** — ``output.success`` and
       ``termination_reason == COMPLETED``.

    2. **Expected output** — if ``task.metadata["expected_output"]`` is set,
       the agent's ``final_answer`` is compared using ``match_mode``:

       * ``"exact"``     — strict equality (``expected == actual``).
       * ``"substring"`` — containment check (``expected in actual``).
         This is the default when ``match_mode`` is absent.

    3. **Expected substring** — if ``task.metadata["expected_substring"]`` is
       set, the agent's final answer must contain that substring regardless of
       ``match_mode``.  Independent of check 2.

    4. **Required trace kinds** — if ``task.metadata["required_trace_kinds"]``
       is set, every listed :class:`~agentevalops.core.schemas.TraceEventKind`
       value string must appear in the trace.

    5. **Required tool names** — if ``task.metadata["required_tool_names"]``
       is set, every listed tool name must appear in the harness's own call
       records for this task run (``context.records``, docs/adr/0006 D14)
       -- never in the agent's self-reported ``AGENT_TOOL_CALL`` events,
       which an agent can emit without calling anything. With no context
       there are no records, and every required tool is missing.

    6. **Final-answer event** — informational only (does not change the
       pass/fail result); if the trace is non-empty and contains no
       ``AGENT_FINAL_ANSWER`` event, the notes record it.
    """

    evaluator_id: str = "deterministic-v1"
    evaluator_kind: str = "deterministic"

    async def evaluate(
        self,
        task: TaskSpec,
        output: AgentOutput,
        trace: list[TraceEvent],
        context: EvaluatorContext | None = None,
    ) -> EvaluationResult:
        """Return a deterministic score for one completed task."""
        parts: list[str] = []
        all_passed = True

        # --- check 1: completion -----------------------------------------
        base_passed = (
            output.success
            and output.termination_reason == TerminationReason.COMPLETED
        )
        parts.append(
            f"termination_reason={output.termination_reason.value}"
        )
        if not base_passed:
            all_passed = False

        # --- check 2: expected_output (match_mode-aware) ------------------
        expected_raw = task.metadata.get("expected_output")
        if expected_raw is not None:
            expected: str = str(expected_raw)
            actual: str = output.final_answer or ""
            match_mode: str = str(task.metadata.get("match_mode", "substring"))

            if match_mode == "exact":
                output_match = expected == actual
                mode_label = "exact"
            else:
                output_match = expected in actual
                mode_label = "substring"

            if output_match:
                parts.append(
                    f"expected_output={mode_label}_match('{expected}')"
                )
            else:
                parts.append(
                    f"expected_output={mode_label}_mismatch"
                    f"(expected='{expected}', got='{actual}')"
                )
                all_passed = False

        # --- check 3: expected_substring ----------------------------------
        expected_sub_raw = task.metadata.get("expected_substring")
        if expected_sub_raw is not None:
            expected_sub: str = str(expected_sub_raw)
            actual_sub: str = output.final_answer or ""
            sub_match = expected_sub in actual_sub
            if sub_match:
                parts.append(
                    f"expected_substring=found('{expected_sub}')"
                )
            else:
                parts.append(
                    f"expected_substring=missing"
                    f"(wanted='{expected_sub}', got='{actual_sub}')"
                )
                all_passed = False

        # --- check 4: required_trace_kinds --------------------------------
        required_kinds_raw = task.metadata.get("required_trace_kinds")
        if required_kinds_raw is not None and isinstance(
            required_kinds_raw, list
        ):
            observed_kind_values = {e.kind.value for e in trace}
            for kind_str in required_kinds_raw:
                if kind_str in observed_kind_values:
                    parts.append(f"required_trace_kind={kind_str}:found")
                else:
                    parts.append(
                        f"required_trace_kind={kind_str}:missing"
                    )
                    all_passed = False

        # --- check 5: required_tool_names ---------------------------------
        required_tools_raw = task.metadata.get("required_tool_names")
        tool_refs: list[Ref] = []
        if required_tools_raw is not None and isinstance(
            required_tools_raw, list
        ):
            # docs/adr/0006 D14: the harness's records, not the agent's word.
            records = _scoped_records(context)
            for tool_name in required_tools_raw:
                made = [r for r in records if r.tool_name == tool_name]
                if made:
                    first = made[0]
                    parts.append(
                        f"required_tool={tool_name}:found(record {first.index})"
                    )
                    tool_refs.append(
                        Ref("tool_calls", first.task_id, first.repetition, first.index)
                    )
                else:
                    parts.append(f"required_tool={tool_name}:missing")
                    all_passed = False

        # --- citations: step indices of AGENT_TERMINAL events -------------
        terminals = [e for e in trace if e.kind == TraceEventKind.AGENT_TERMINAL]
        citations = [e.step_index for e in terminals]
        refs = [Ref("trace", e.task_id, e.repetition, e.step_index) for e in terminals]
        refs.extend(tool_refs)

        # --- check 6: informational AGENT_FINAL_ANSWER presence ----------
        if trace and not any(
            e.kind == TraceEventKind.AGENT_FINAL_ANSWER for e in trace
        ):
            parts.append("no AGENT_FINAL_ANSWER event in trace")

        passed = all_passed
        score = 1.0 if passed else 0.0

        return EvaluationResult(
            evaluator_id=self.evaluator_id,
            evaluator_kind=self.evaluator_kind,
            score=score,
            passed=passed,
            citations=citations,
            notes="; ".join(parts),
            refs=refs,
        )
