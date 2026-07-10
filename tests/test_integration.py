"""End-to-end tests: generated headers run through the real C preprocessor.

Golden .cursed files carry their own invocation specs as comments:

    #? MACRO(args) => expected expansion
    #? MACRO(args)          # multi-assert form
    #=> expected fragment
    #=> another expected fragment

Every spec compiles the template, invokes the macro from a C snippet,
runs `cc -E -P`, and asserts each expected fragment appears (whitespace-
canonicalized) in the expansion.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cursedpp.emitter import compile_template

GOLDEN = Path(__file__).parent / "golden"

CC = shutil.which("cc") or shutil.which("gcc")


def _boost_available() -> bool:
    if CC is None:
        return False
    probe = subprocess.run(
        [CC, "-E", "-P", "-x", "c", "-"],
        input="#include <boost/preprocessor.hpp>\n",
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


requires_boost = pytest.mark.skipif(
    not _boost_available(), reason="boost/preprocessor.hpp not available"
)


def canon(text: str) -> str:
    """Whitespace-canonical form: the preprocessor may add/drop spaces around
    punctuation, but never inside identifiers - so compare modulo that."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ?([(),;{}|]) ?", r"\1", text)


def preprocess_src(tmp_path: Path, source: str, stem: str, invocation: str) -> str:
    """Compile a template, include it from a snippet, run cc -E -P."""
    header = tmp_path / f"{stem}.h"
    result = compile_template(source, f"{stem}.cursed")
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp_path / result.runtime_name).write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    run = subprocess.run(
        [CC, "-E", "-P", str(snippet)], capture_output=True, text=True, check=True
    )
    return canon(run.stdout)


# ── spec harness: invocations + expectations live in the .cursed files ──


def parse_specs(text: str) -> list[tuple[str, list[str]]]:
    """Extract (invocation, [expected, ...]) cases from #? / #=> comments."""
    cases: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#?"):
            body = stripped[2:].strip()
            if "=>" in body:
                invocation, expected = body.split("=>", 1)
                cases.append((invocation.strip(), [expected.strip()]))
            else:
                cases.append((body, []))
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
    for cursed in sorted(GOLDEN.glob("*.cursed")):
        for i, (invocation, expecteds) in enumerate(parse_specs(cursed.read_text())):
            params.append(
                pytest.param(cursed, invocation, expecteds, id=f"{cursed.stem}-{i}")
            )
    return params


def test_every_golden_template_has_specs():
    missing = [p.name for p in GOLDEN.glob("*.cursed") if not parse_specs(p.read_text())]
    assert not missing, f"golden templates without #? specs: {missing}"


@requires_boost
@pytest.mark.parametrize(("cursed", "invocation", "expecteds"), spec_params())
def test_spec(tmp_path, cursed, invocation, expecteds):
    out = preprocess_src(tmp_path, cursed.read_text(), cursed.stem, invocation)
    for expected in expecteds:
        assert canon(expected) in out, f"{invocation} missing: {expected}"


# ── behaviors without golden files ──────────────────────────────────


COLLAPSED_SRC = (
    "macro DECLARE_INTS(xs: seq<token>)\n@for x in xs\nint {{x}};\n@end\nend\n"
    "macro DECLARE_FLOATS(ys: seq<token>)\n@for y in ys\nfloat {{y}};\n@end\nend\n"
)


@requires_boost
def test_collapsed_shared_helper_expands_correctly(tmp_path):
    out = preprocess_src(
        tmp_path, COLLAPSED_SRC, "coll", "DECLARE_INTS((a)(b))\nDECLARE_FLOATS((u)(v))"
    )
    assert canon("int a; int b;") in out
    assert canon("float u; float v;") in out


@requires_boost
def test_loop_free_vars_through_data_slot(tmp_path):
    src = "macro TAG(prefix, xs: seq<token>)\n@for x in xs\nf({{prefix}}, {{x}});\n@end\nend\n"
    out = preprocess_src(tmp_path, src, "tag", "TAG(dbg, (a)(b))")
    assert canon("f(dbg, a); f(dbg, b);") in out


@requires_boost
def test_let_join_reuse(tmp_path):
    src = (
        "macro CALL2(fn, args: seq<tuple<type, argname>>)\n"
        '@let joined := @join args with ", ": {{argname}}@end\n'
        "{{fn}}({{joined}}, {{joined}})\n"
        "end\n"
    )
    out = preprocess_src(tmp_path, src, "call2", "CALL2(f, ((int, a))((int, b)))")
    assert canon("f(a, b, a, b)") in out


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
