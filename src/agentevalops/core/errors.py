"""AgentEvalOps exception hierarchy."""

from __future__ import annotations


class AgentEvalOpsError(Exception):
    """Base class for all AgentEvalOps errors."""


class ConfigurationError(AgentEvalOpsError):
    """Invalid configuration: missing required fields, conflicting options, etc."""


class RunError(AgentEvalOpsError):
    """Error that occurred during an evaluation run."""


class ResourceLimitExceeded(RunError):
    """An agent exceeded a ``ResourceLimits`` threshold during a run."""


class InfrastructureError(RunError):
    """Sandbox crash, model API failure, or other infrastructure problem.

    Agent-level failures (wrong answer, agent giving up) are NOT this
    exception — those are captured in ``AgentOutput.termination_reason``.
    """


class BundleError(AgentEvalOpsError):
    """Error reading or writing a result bundle."""


class PolicyError(AgentEvalOpsError):
    """Error in policy specification or policy evaluation."""
