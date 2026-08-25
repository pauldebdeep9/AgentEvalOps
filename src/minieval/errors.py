"""Exceptions for minieval.

Two kinds of failure, deliberately not unified under a shared base: a
failure the harness caused (``InfrastructureError``) and a failure in how
the harness was told to run (``ConfigurationError``). Catching ``Exception``
where that distinction matters is the wrong fix, not a excuse for a shared
parent.
"""

from __future__ import annotations


class InfrastructureError(Exception):
    """The harness or a dependency failed, not the agent under test.

    Raised by an ``AgentRunner`` when it cannot complete an attempt for
    reasons that have nothing to do with the agent's competence — a model
    API error, a network failure, a malformed response. The orchestrator
    catches this per attempt and records ``EvaluationResult.evaluated=False``.
    """


class ConfigurationError(Exception):
    """A run was configured in a way that cannot be executed.

    Raised while constructing a run — an unknown config key, a missing
    required field, an unrecognised runner name — never mid-attempt.

    Deliberately not caught per attempt by the orchestrator. A setup mistake
    is one failure, not N of them: catching this alongside
    ``InfrastructureError`` inside the repeats loop would turn a single typo
    into N identical ``evaluated=False`` results, which hides a broken run
    behind a plausible-looking partition instead of aborting it.
    """
