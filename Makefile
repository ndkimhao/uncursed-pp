MISE := mise exec --
BOOST_PP_DIR := .boost-pp
BOOST_PP_REF := boost-1.90.0

.PHONY: setup check test test-unit test-integration typecheck example speccheck eval-smoke regen-golden clean boost-pp

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

# Run the standalone spec checker over every template - exercises the
# real CLI end to end (pytest covers the same specs through the API).
# CHECK_CC overrides the compiler, e.g. `make speccheck CHECK_CC=clang-19`
speccheck: boost-pp
	$(MISE) uv run uncursed-pp-check tests/golden examples $(if $(CHECK_CC),--cc $(CHECK_CC)) -- -I $(BOOST_PP_DIR)/include

# Smoke the uncursed-pp-eval CLI: invoke + --update-specs, each with
# and without --format (needs clang-format for the format legs).
eval-smoke: boost-pp
	bash scripts/eval_smoke.sh

# Regenerate every golden/example .h from its template. Mechanics only:
# goldens are updated deliberately - READ the diff before committing.
regen-golden:
	$(MISE) uv run python scripts/regen_goldens.py

example:
	$(MISE) uv run uncursed-pp examples/combined/bitflags.uncursed -o /tmp/example.h --emit-runtime
	@echo "--- /tmp/example.h ---"
	@cat /tmp/example.h

clean:
	rm -rf .venv .pytest_cache dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
