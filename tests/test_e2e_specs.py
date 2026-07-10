"""Spec-driven e2e: golden templates carry their own invocation specs.

    #?  MACRO(args)
    #=>     expected expansion (line 1)
    #=>     expected expansion (line 2)

    #?! MACRO(bad-args)      # expected-failure: preprocessing must error

The #=> lines together are the COMPLETE expected expansion: the harness
compiles the template, invokes the macro from a C snippet, runs
`cc -E -P` (against the vendored Boost.PP), and requires the whole
preprocessed output to equal the joined expectation (whitespace-
canonicalized) - a missing or extra token fails the spec. '#?!' cases
assert the documented failure modes really fail.
"""

from typing import Any

import pytest

from conftest import canon, golden_id, golden_templates, preprocess_src, requires_boost
from uncursed_pp.speccheck import parse_specs as parse_specs  # re-export for test_harness


def spec_params() -> list[Any]:
    params = []
    for template in golden_templates():
        for i, (invocation, expecteds, expect_failure) in enumerate(
            parse_specs(template.read_text())
        ):
            params.append(
                pytest.param(
                    template,
                    invocation,
                    expecteds,
                    expect_failure,
                    id=f"{golden_id(template)}-{i}",
                )
            )
    return params


def test_every_golden_template_has_specs():
    missing = [golden_id(p) for p in golden_templates() if not parse_specs(p.read_text())]
    assert not missing, f"golden templates without #? specs: {missing}"


def test_every_golden_macro_is_exercised():
    """Each macro a golden defines must be invoked by a spec, or called
    from another macro in the same template (composition). String/char
    literals are stripped first so a name inside emitted text can't fool
    the check."""
    import re

    from uncursed_pp.emitter import C_LITERAL_PATTERN

    unexercised = []
    for template in golden_templates():
        text = template.read_text()
        macros = re.findall(r"^@macro\s+(\w+)\s*\(", text, flags=re.MULTILINE)
        invocations = " ".join(inv for inv, _, _ in parse_specs(text))
        bodies = re.sub(r"^@macro\s+\w+\s*\(.*$", "", text, flags=re.MULTILINE)
        bodies = "\n".join(
            line for line in bodies.splitlines() if not line.strip().startswith("#")
        )
        bodies = re.sub(C_LITERAL_PATTERN, " ", bodies)
        for name in macros:
            if not re.search(rf"\b{name}\s*\(", invocations + " " + bodies):
                unexercised.append(f"{golden_id(template)}:{name}")
    assert not unexercised, f"macros never invoked by any spec: {unexercised}"


@requires_boost
@pytest.mark.parametrize(
    ("template", "invocation", "expecteds", "expect_failure"), spec_params()
)
def test_spec(tmp_path, template, invocation, expecteds, expect_failure):
    if expect_failure:
        with pytest.raises(AssertionError, match="preprocessing failed"):
            preprocess_src(tmp_path, template.read_text(), template.stem, invocation)
        return
    out = preprocess_src(tmp_path, template.read_text(), template.stem, invocation)
    expected = canon(" ".join(expecteds))
    assert expected == out, (
        f"{invocation}\n  expected: {expected}\n  actual:   {out}"
    )


@requires_boost
def test_adjacency_no_paste_e2e(tmp_path):
    src = (
        "@macro G(f: tuple<t, n>, xs: seq<token>)\n"
        "pre{{f.n}} mid{{f.t}}\n"
        "@for x in xs\nitem{{x}};\n@end\n"
        "@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "adj", "G((int, age), (a)(b))")
    assert canon("pre age mid int item a; item b;") == out
