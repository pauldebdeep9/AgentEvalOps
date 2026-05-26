.PHONY: install lint typecheck test test-cov check smoke build clean

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check src/ tests/

typecheck:
	mypy src

test:
	pytest

test-cov:
	pytest --cov=agentevalops --cov-report=term-missing

check: lint typecheck test

smoke:
	rm -rf runs/make-smoke
	agentevalops run --config configs/toy_smoke.yaml --output runs/make-smoke
	agentevalops validate-bundle --bundle runs/make-smoke
	agentevalops replay --bundle runs/make-smoke
	rm -rf runs/make-smoke

build:
	python -m build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	rm -rf src/*.egg-info src/agentevalops.egg-info
	rm -rf runs/make-smoke
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
