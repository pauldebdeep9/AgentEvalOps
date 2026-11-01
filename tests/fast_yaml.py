"""``yaml.safe_load`` and ``yaml.safe_dump`` on PyYAML's libyaml-backed
loader and dumper when the wheel has them, for the tests.

The same safe loading and dumping, about seven times faster on the seed
pack's scenarios.yaml, which the load-time guard tests parse and re-write
for every case: those calls were roughly half the suite's profiled time
on 2026-11-02. The package's own loaders already make this choice
(``domain_packs/seed/pack.py``, ``catalogue/loader.py``), and
``test_the_pack_loads_on_pyyaml_pure_python_parser`` still covers the
pure-Python fallback.
"""

from __future__ import annotations

from typing import Any

import yaml

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def safe_load(stream: Any) -> Any:
    return yaml.load(stream, Loader=_LOADER)


def safe_dump(data: Any, stream: Any = None, **kwargs: Any) -> Any:
    return yaml.dump(data, stream, Dumper=_DUMPER, **kwargs)
