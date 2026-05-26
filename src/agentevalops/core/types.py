"""Stable identifier types for AgentEvalOps runtime objects.

These are ``NewType`` wrappers around ``str`` to prevent accidental mixing
of identifiers at type-check time.  They are fully transparent at runtime.
"""

from __future__ import annotations

from typing import NewType

# ---------------------------------------------------------------------------
# Identifier types
# ---------------------------------------------------------------------------

RunId = NewType("RunId", str)
"""Unique identifier for a single evaluation run."""

TaskId = NewType("TaskId", str)
"""Unique identifier for a task within a benchmark."""

AgentId = NewType("AgentId", str)
"""Stable identifier for an agent implementation (e.g. "langgraph-claude-3-7")."""

BackendId = NewType("BackendId", str)
"""Identifies the cloud/local execution backend."""

# ---------------------------------------------------------------------------
# BackendId constants — only "local" and "aws" are first-class in v0.1
# ---------------------------------------------------------------------------

BACKEND_LOCAL: BackendId = BackendId("local")
BACKEND_AWS: BackendId = BackendId("aws")
