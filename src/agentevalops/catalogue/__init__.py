"""Control catalogue: versioned source documents and the controls cited from
them, as data (ADR-0005 D5).

Identifiers and this project's own labels only -- no third-party text. Lives
in its own layer between the adapters and ``core`` (ADR-0006 D8's tier) and
imports nothing from ``agentevalops`` except ``core``. Nothing uses it until
R24, when scenarios start citing control IDs.
"""

from agentevalops.catalogue.loader import (
    Catalogue,
    CatalogueError,
    Control,
    Source,
    load_catalogue,
)

__all__ = ["Catalogue", "CatalogueError", "Control", "Source", "load_catalogue"]
