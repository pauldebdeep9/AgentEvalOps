"""Domain packs: a domain's tools, cases and scenarios, run by the
``agentic-baseline`` benchmark (docs/adr/0005 D9, docs/adr/0006 D23).

A pack is discovered through the ``agentevalops.domain_packs`` entry-point
group (D24) or registered as a built-in, and resolved by name from
``benchmark_options.pack``. This package is an adapter family of its own: it
imports ``core``, ``environments`` and ``catalogue``, and nothing imports it
except the registry.
"""

from agentevalops.core.errors import ConfigurationError


class DomainPackError(ConfigurationError):
    """A domain pack's data is malformed or breaks a pack rule."""


__all__ = ["DomainPackError"]
