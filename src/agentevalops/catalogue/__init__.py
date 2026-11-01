"""Control catalogue: versioned source documents and the controls cited from
them, as data (ADR-0005 D5).

Identifiers and this project's own labels only -- no third-party text. Lives
in its own layer between the adapters and ``core`` (ADR-0006 D8's tier) and
imports nothing from ``agentevalops`` except ``core``. Three sources: the
MGF-Agentic sections and the Starter Kit's risks the seed pack's scenarios
cite (R22, R24), and the testing framework's 112 process checks with their
classification and evidence pointers (docs/adr/0007 D10, R28).
"""

from agentevalops.catalogue.loader import (
    BUNDLE_0_6_FILES,
    CLASSIFICATIONS,
    SCENARIO_CLASSES,
    SCENARIOS_EVIDENCE,
    Catalogue,
    CatalogueError,
    Control,
    Source,
    load_catalogue,
)

__all__ = [
    "BUNDLE_0_6_FILES",
    "CLASSIFICATIONS",
    "SCENARIOS_EVIDENCE",
    "SCENARIO_CLASSES",
    "Catalogue",
    "CatalogueError",
    "Control",
    "Source",
    "load_catalogue",
]
