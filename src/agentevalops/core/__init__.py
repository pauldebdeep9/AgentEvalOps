"""AgentEvalOps core layer — protocols, schemas, types, and errors.

This package is the load-bearing contract layer.  It has no dependencies
on any cloud provider, model provider, or agent framework.

Import from here for the public API::

    from agentevalops.core import AgentRunner, TaskSpec, RunId
"""

from __future__ import annotations

from agentevalops.core import errors, protocols, schemas, types
from agentevalops.core.errors import (
    AgentEvalOpsError,
    BundleError,
    ConfigurationError,
    InfrastructureError,
    PolicyError,
    ResourceLimitExceeded,
    RunError,
)
from agentevalops.core.protocols import (
    AgentRunner,
    ArtifactStore,
    BenchmarkAdapter,
    CloudBackend,
    Evaluator,
    PolicyChecker,
    ReportGenerator,
    Scorer,
    TraceStore,
)
from agentevalops.core.schemas import (
    AgentInput,
    AgentOutput,
    EvaluationResult,
    PolicySpec,
    PolicyVerdict,
    ResourceLimits,
    ResultBundleMetadata,
    RunConfig,
    ScoreResult,
    TaskSpec,
    TerminationReason,
    TraceEvent,
    TraceEventKind,
    Verdict,
)
from agentevalops.core.types import (
    BACKEND_AWS,
    BACKEND_LOCAL,
    AgentId,
    BackendId,
    RunId,
    TaskId,
)

__all__ = [
    # sub-modules
    "errors",
    "protocols",
    "schemas",
    "types",
    # errors
    "AgentEvalOpsError",
    "BundleError",
    "ConfigurationError",
    "InfrastructureError",
    "PolicyError",
    "ResourceLimitExceeded",
    "RunError",
    # protocols
    "AgentRunner",
    "ArtifactStore",
    "BenchmarkAdapter",
    "CloudBackend",
    "Evaluator",
    "PolicyChecker",
    "ReportGenerator",
    "Scorer",
    "TraceStore",
    # schemas
    "AgentInput",
    "AgentOutput",
    "EvaluationResult",
    "PolicySpec",
    "PolicyVerdict",
    "ResourceLimits",
    "ResultBundleMetadata",
    "RunConfig",
    "ScoreResult",
    "TaskSpec",
    "TerminationReason",
    "TraceEvent",
    "TraceEventKind",
    "Verdict",
    # types
    "BACKEND_AWS",
    "BACKEND_LOCAL",
    "AgentId",
    "BackendId",
    "RunId",
    "TaskId",
]
