MISE := mise exec --
BOOST_PP_DIR := .boost-pp
BOOST_PP_REF := boost-1.90.0

.PHONY: setup check test test-unit test-integration typecheck example clean boost-pp

check: typecheck test

setup: boost-pp
	mise trust --quiet || true
	mise install
	$(MISE) uv sync

# Vendored Boost.Preprocessor (header-only) so tests never depend on a
# system boost. Shallow clone; gitignored.
boost-pp: $(BOOST_PP_DIR)

$(BOOST_PP_DIR):
	git clone --depth 1 --branch $(BOOST_PP_REF) https://github.com/boostorg/preprocessor.git $(BOOST_PP_DIR)

test: boost-pp
	$(MISE) uv run pytest

test-unit:
	$(MISE) uv run pytest --ignore=tests/test_e2e_specs.py

test-integration: boost-pp
	$(MISE) uv run pytest tests/test_e2e_specs.py

typecheck:
	$(MISE) uv run mypy

example:
	$(MISE) uv run cursedpp examples/example.cursed -o /tmp/example.h
	@echo "--- /tmp/example.h ---"
	@cat /tmp/example.h

clean:
	rm -rf .venv .pytest_cache dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
