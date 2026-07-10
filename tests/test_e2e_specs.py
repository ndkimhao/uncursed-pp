"""Spec-driven e2e: golden templates carry their own invocation specs.

    #?  MACRO(args)
    #=>     expected expansion (line 1)
    #=>     expected expansion (line 2)

The #=> lines together are the COMPLETE expected expansion: the harness
compiles the template, invokes the macro from a C snippet, runs
`cc -E -P` (against the vendored Boost.PP), and requires the whole
preprocessed output to equal the joined expectation (whitespace-
canonicalized) - a missing or extra token fails the spec.
"""

from pathlib import Path

import pytest

from conftest import canon, golden_id, golden_templates, preprocess_src, requires_boost


def parse_specs(text: str) -> list[tuple[str, list[str]]]:
    """Extract (invocation, [expected, ...]) cases from #? / #=> comments."""
    cases: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#?"):
            invocation = stripped[2:].strip()
            if "=>" in invocation:
                raise ValueError(
                    f"put the expectation on its own '#=>' line: {stripped!r}"
                )
            cases.append((invocation, []))
        elif stripped.startswith("#=>"):
            if not cases:
                raise ValueError("#=> before any #? line")
            cases[-1][1].append(stripped[3:].strip())
    for invocation, expecteds in cases:
        if not expecteds:
            raise ValueError(f"spec {invocation!r} has no expected output")
    return cases


def spec_params() -> list:
    params = []
    for cursed in golden_templates():
        for i, (invocation, expecteds) in enumerate(parse_specs(cursed.read_text())):
            params.append(
                pytest.param(
                    cursed, invocation, expecteds, id=f"{golden_id(cursed)}-{i}"
                )
            )
    return params


def test_every_golden_template_has_specs():
    missing = [golden_id(p) for p in golden_templates() if not parse_specs(p.read_text())]
    assert not missing, f"golden templates without #? specs: {missing}"


@requires_boost
@pytest.mark.parametrize(("cursed", "invocation", "expecteds"), spec_params())
def test_spec(tmp_path, cursed, invocation, expecteds):
    out = preprocess_src(tmp_path, cursed.read_text(), cursed.stem, invocation)
    expected = canon(" ".join(expecteds))
    assert expected == out, (
        f"{invocation}\n  expected: {expected}\n  actual:   {out}"
    )


@requires_boost
def test_example_file_compiles_and_all_macros_expand(tmp_path):
    """The shipped feature-tour example works end-to-end."""
    source = (Path(__file__).parent.parent / "examples" / "example.cursed").read_text()
    invocations = "\n".join(
        [
            "DECLARE_FIELDS(((int, x))((float, y)))",
            "PROTO(draw, ((struct ctx *, ctx))((int, flags)))",
            "GETTER((int, age))",
            "PAIR(((pair<int,int>), b))",
            "CTOR(w, ((int, x))((int, y)))",
            "LOG(m, WARN)",
            "MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))",
            "NORMALIZE(a, (b,c), d)",
        ]
    )
    out = preprocess_src(tmp_path, source, "example", invocations)
    for expected in [
        "int x; float y;",
        "void draw(struct ctx * ctx, int flags);",
        "int get_age(const struct self *s) { return s->age; }",
        "S{ pair<int,int> | b }",
        "w_init(x, y)",
        'fprintf(stderr, "[" "WARN" "] %s\\n", m);',
        "struct widget w3 = { 20, 50, BOLD };",
        "S{ (a, omit), (b,c), (d, omit) }",
    ]:
        assert canon(expected) in out, expected
