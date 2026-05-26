"""Smoke tests: package is importable and exposes __version__."""

import agentevalops
import agentevalops.core


def test_package_importable() -> None:
    assert agentevalops is not None


def test_version_exists() -> None:
    assert hasattr(agentevalops, "__version__")


def test_version_is_string() -> None:
    assert isinstance(agentevalops.__version__, str)
    assert agentevalops.__version__ != ""


def test_core_importable() -> None:
    assert agentevalops.core is not None
