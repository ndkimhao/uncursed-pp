"""End-to-end tests: generated headers run through the real C preprocessor."""

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


def preprocess(tmp_path: Path, cursed_name: str, invocation: str) -> str:
    """Compile a golden template, include it from a snippet, run cc -E -P."""
    source = (GOLDEN / f"{cursed_name}.cursed").read_text()
    header = tmp_path / f"{cursed_name}.h"
    result = compile_template(source, f"{cursed_name}.cursed")
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp_path / result.runtime_name).write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    result = subprocess.run(
        [CC, "-E", "-P", str(snippet)],
        capture_output=True,
        text=True,
        check=True,
    )
    return canon(result.stdout)


def canon(text: str) -> str:
    """Whitespace-canonical form: the preprocessor may add/drop spaces around
    punctuation, but never inside identifiers - so compare modulo that."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ?([(),;{}|]) ?", r"\1", text)


@requires_boost
def test_declare_fields_expansion(tmp_path):
    expanded = preprocess(
        tmp_path, "declare_fields", "DECLARE_FIELDS(((int, x))((float, y))((char *, name)))"
    )
    assert canon("int x; float y; char * name;") in expanded


@requires_boost
def test_proto_expansion(tmp_path):
    expanded = preprocess(
        tmp_path, "proto", "PROTO(draw, ((struct ctx *, ctx))((int, flags)))"
    )
    assert canon("void draw(struct ctx * ctx, int flags);") in expanded


@requires_boost
def test_getter_concat_expansion(tmp_path):
    expanded = preprocess(tmp_path, "getter", "GETTER((int, age))")
    assert canon("int get_age(const struct self *s) { return s->age; }") in expanded


@requires_boost
def test_pair_remove_parens_expansion(tmp_path):
    expanded = preprocess(tmp_path, "pair", "PAIR(((pair<int,int>), b))")
    assert canon("S{ pair<int,int> | b }") in expanded


@requires_boost
def test_pair_without_parens_passthrough(tmp_path):
    expanded = preprocess(tmp_path, "pair", "PAIR((a, b))")
    assert canon("S{ a | b }") in expanded


@requires_boost
def test_ctor_single_vs_multi_arg(tmp_path):
    single = preprocess(tmp_path, "ctor", "CTOR(w, ((int, x)))")
    assert canon("explicit_single_arg_init(w)") in single
    multi = preprocess(tmp_path, "ctor", "CTOR(w, ((int, x))((int, y)))")
    assert canon("w_init(x, y)") in multi


@requires_boost
def test_norm_strips_parens_iff_present(tmp_path):
    stripped = preprocess(tmp_path, "norm", "NORM((a, b))")
    assert canon("a, b") in stripped
    passthrough = preprocess(tmp_path, "norm", "NORM(q)")
    assert canon("q") in passthrough


LOG_SRC = 'macro LOG(msg, level = INFO, out = stderr)\nfprintf({{out}}, "[" #{{level}} "] %s\\n", {{msg}});\nend\n'

WIDGET_SRC = (
    "macro MAKE_WIDGET(name, named WIDTH = 100, named HEIGHT = 50, named FLAGS = )\n"
    "struct widget {{name}} = { {{WIDTH}}, {{HEIGHT}}, {{FLAGS}} };\n"
    "end\n"
)


def preprocess_src(tmp_path: Path, source: str, stem: str, invocation: str) -> str:
    header = tmp_path / f"{stem}.h"
    result = compile_template(source, f"{stem}.cursed")
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp_path / result.runtime_name).write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    result = subprocess.run(
        [CC, "-E", "-P", str(snippet)], capture_output=True, text=True, check=True
    )
    return canon(result.stdout)


@requires_boost
def test_log_default_args(tmp_path):
    assert canon("fprintf(stderr, \"[\" \"INFO\" \"] %s\\n\", m);") in preprocess_src(
        tmp_path, LOG_SRC, "log", "LOG(m)"
    )
    assert canon("fprintf(stdout, \"[\" \"WARN\" \"] %s\\n\", m);") in preprocess_src(
        tmp_path, LOG_SRC, "log2", "LOG(m, WARN, stdout)"
    )


@requires_boost
def test_widget_named_args(tmp_path):
    assert canon("struct widget w1 = { 100, 50, };") in preprocess_src(
        tmp_path, WIDGET_SRC, "w1", "MAKE_WIDGET(w1)"
    )
    assert canon("struct widget w2 = { 100, 80, };") in preprocess_src(
        tmp_path, WIDGET_SRC, "w2", "MAKE_WIDGET(w2, HEIGHT(80))"
    )
    assert canon("struct widget w3 = { 20, 50, BOLD };") in preprocess_src(
        tmp_path, WIDGET_SRC, "w3", "MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))"
    )


FOO_VARIADIC_SRC = (
    "macro FOO(items: variadic)\n"
    'S{ @join items as it with ", ": @if is_paren(it) {{it}} @else ({{it}}, omit) @end@end }\n'
    "end\n"
)


@requires_boost
def test_variadic_paren_normalization(tmp_path):
    out = preprocess_src(tmp_path, FOO_VARIADIC_SRC, "foo", "FOO(a, (b,c), d)")
    assert canon("S{ (a, omit), (b,c), (d, omit) }") in out


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
def test_reflection_system(tmp_path):
    """The reflect golden: one field list -> struct + metadata + printer."""
    source = (GOLDEN / "reflect.cursed").read_text()
    out = preprocess_src(
        tmp_path, source, "reflect", 'REFLECT(Point, (int, x, "%d"), (float, y, "%f"))'
    )
    assert canon("typedef struct { int x; float y; } Point;") in out
    assert canon('{ "x", "int", offsetof(Point, x) },') in out
    assert canon('{ "y", "float", offsetof(Point, y) },') in out
    assert canon("enum { Point_field_count = 2 };") in out
    assert canon('printf("  " "x" " = " "%d" "\\n", v->x);') in out
    assert canon("static void print_Point(const Point *v)") in out
