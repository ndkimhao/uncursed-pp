MISE := mise exec --

.PHONY: setup test test-unit test-integration example clean

setup:
	mise trust --quiet || true
	mise install
	$(MISE) uv sync

test:
	$(MISE) uv run pytest

test-unit:
	$(MISE) uv run pytest --ignore=tests/test_integration.py

test-integration:
	$(MISE) uv run pytest tests/test_integration.py

example:
	$(MISE) uv run cursedpp examples/example.cursed -o /tmp/example.h
	@echo "--- /tmp/example.h ---"
	@cat /tmp/example.h

clean:
	rm -rf .venv .pytest_cache dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
