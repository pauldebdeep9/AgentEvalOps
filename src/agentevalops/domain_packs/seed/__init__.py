"""The seed domain: synthetic public-sector case handling (ADR-0005 D9).

Six tools over a file of applications to a synthetic grant scheme; eight
seeded cases with their expected outcomes; ten scenarios across task
execution and tool calling, each with a known-good and a known-bad
trajectory. All of it is data under ``data/``, validated when the pack is
built.
"""

from agentevalops.domain_packs.seed.pack import SeedDomainPack

__all__ = ["SeedDomainPack"]
