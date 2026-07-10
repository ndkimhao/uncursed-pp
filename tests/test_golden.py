"""Golden-file tests: every tests/golden/**/*.cursed must compile to
exactly its committed .h neighbor. Goldens are organized by category
(basics/, loops/, conditionals/, expressions/, args/, variadic/, compose/).
"""

import pytest

from conftest import golden_id, golden_templates
from cursedpp.emitter import compile_source


@pytest.mark.parametrize("cursed", golden_templates(), ids=golden_id)
def test_golden(cursed):
    expected = cursed.with_suffix(".h").read_text()
    assert compile_source(cursed.read_text(), cursed.name) == expected


def test_every_golden_template_has_a_header():
    missing = [golden_id(p) for p in golden_templates() if not p.with_suffix(".h").exists()]
    assert not missing, f"golden templates without .h: {missing}"
