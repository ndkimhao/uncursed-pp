"""Golden-file tests: every tests/golden/**/*.uncursed must compile to
exactly its committed .h neighbor. Goldens are organized by category
(basics/, loops/, conditionals/, expressions/, args/, variadic/, compose/).
"""

import pytest

from conftest import golden_id, golden_templates
from uncursed_pp.emitter import compile_source


@pytest.mark.parametrize("template", golden_templates(), ids=golden_id)
def test_golden(template):
    expected = template.with_suffix(".h").read_text()
    assert compile_source(template.read_text(), template.name) == expected


def test_every_golden_template_has_a_header():
    missing = [golden_id(p) for p in golden_templates() if not p.with_suffix(".h").exists()]
    assert not missing, f"golden templates without .h: {missing}"


def test_no_orphaned_golden_headers():
    from conftest import GOLDEN

    stray = [
        str(h.relative_to(GOLDEN))
        for h in GOLDEN.rglob("*.h")
        if h.name != "uncursed_pp_runtime.h" and not h.with_suffix(".uncursed").exists()
    ]
    assert not stray, f"golden headers without a .uncursed source: {stray}"
