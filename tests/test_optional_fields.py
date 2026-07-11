"""Optional trailing tuple fields: `tuple<$a, $b = def, $c?>`.

`= def` fills a default when the call site omits the field; `?` fields
are truly absent (query with has($t.$f); accessing an absent field
yields zero tokens). Codegen shape and runtime behavior live in
tests/golden/args/optional_fields.*"""

from pathlib import Path

import pytest

from conftest import CC, canon, preprocess_src
from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import Has, If, TupleT
from uncursed_pp.parser import UncursedPpError, parse_file

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")


# ── parsing ──────────────────────────────────────────────────────────


def test_parse_field_kinds() -> None:
    src = "@macro F($t: tuple<$name, $type = int, $flags?>)\n{{$t.$name}}\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    t = macro.params[0].type
    assert t == TupleT(("name", "type", "flags"), (None, "int", None), (2,))
    assert t.required == 1 and t.has_optional


def test_plain_tuples_are_unchanged() -> None:
    src = "@macro F($t: tuple<$a, $b>)\n{{$t.$a}}\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    assert macro.params[0].type == TupleT(("a", "b"))


def test_required_after_optional_is_an_error() -> None:
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro F($t: tuple<$a = 1, $b>)\nx\n@endmacro\n", "t.uncursed")
    assert "optional" in str(excinfo.value)


def test_has_parses_as_condition() -> None:
    src = (
        "@macro F($t: tuple<$a, $b?>)\n@if has($t.$b)\nx\n@end\n@endmacro\n"
    )
    [macro] = parse_file(src, "t.uncursed").macros
    node = next(n for n in macro.body if isinstance(n, If))
    assert isinstance(node.cond, Has)


def test_has_needs_a_field_access() -> None:
    with pytest.raises(UncursedPpError):
        parse_file("@macro F($t: tuple<$a, $b?>)\n{{has($t)}}\n@endmacro\n", "t.uncursed")


# ── defaults: fill at C compile time ─────────────────────────────────

FIELD = (
    "@macro FIELD($f: tuple<$name, $type = int, $qual = >)\n"
    "{{$f.$qual}} {{$f.$type}} {{$f.$name}};\n@endmacro\n"
)


def test_defaults_fill_omitted_fields(tmp_path: Path) -> None:
    out = preprocess_src(
        tmp_path, FIELD, "of", "FIELD((x))\nFIELD((y, float))\nFIELD((z, char, const))"
    )
    assert canon("int x;") in out
    assert canon("float y;") in out
    assert canon("const char z;") in out


def test_too_few_fields_is_loud(tmp_path: Path) -> None:
    src = "@macro P($t: tuple<$a, $b, $c = 1>)\n{{$t.$a}}{{$t.$b}}{{$t.$c}}\n@endmacro\n"
    with pytest.raises(AssertionError, match="preprocessing failed"):
        preprocess_src(tmp_path, src, "tf", "P((only))")


# ── '?' fields: presence probing ─────────────────────────────────────

MAYBE = (
    "@macro DECL($t: tuple<$name, $init?>)\n"
    "@if has($t.$init)\nint {{$t.$name}} = {{$t.$init}};\n"
    "@else\nint {{$t.$name}};\n@end\n@endmacro\n"
)


def test_has_branches_on_presence(tmp_path: Path) -> None:
    out = preprocess_src(tmp_path, MAYBE, "mb", "DECL((a, 42))\nDECL((b))")
    assert canon("int a = 42;") in out
    assert canon("int b;") in out


def test_absent_field_access_is_empty(tmp_path: Path) -> None:
    src = "@macro A($t: tuple<$x, $y?>)\npre [{{$t.$y}}] post\n@endmacro\n"
    out = preprocess_src(tmp_path, src, "ab", "A((v))")
    assert canon("pre [] post") in out


def test_has_in_interpolation(tmp_path: Path) -> None:
    src = "@macro H($t: tuple<$x, $y?>)\ngot={{has($t.$y)}}\n@endmacro\n"
    out = preprocess_src(tmp_path, src, "hi", "H((v, w))\nH((v))")
    assert canon("got=1") in out and canon("got=0") in out


def test_has_on_a_non_maybe_field_is_an_error() -> None:
    src = "@macro F($t: tuple<$a, $b = 1, $c?>)\n{{has($t.$b)}}\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "'?'" in str(excinfo.value)


def test_whole_value_use_of_maybe_tuple_is_an_error() -> None:
    src = "@macro F($t: tuple<$a, $b?>)\n{{$t}}\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "?" in str(excinfo.value)


# ── composition ──────────────────────────────────────────────────────


def test_composes_inside_seq_loops(tmp_path: Path) -> None:
    src = (
        "@macro ROWS($fs: seq<tuple<$name, $type = int>>)\n"
        "@for ($name, $type) in $fs\n{{$type}} {{$name}};\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "cs", "ROWS(((a))((b, float)))")
    assert canon("int a; float b;") in out


def test_composes_with_loop_var_and_has(tmp_path: Path) -> None:
    src = (
        "@macro INITS($fs: seq<tuple<$name, $init?>>)\n"
        "@for $f in $fs\n"
        "@if has($f.$init)\nint {{$f.$name}} = {{$f.$init}};\n"
        "@else\nint {{$f.$name}};\n@end\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "cv", "INITS(((a, 1))((b)))")
    assert canon("int a = 1; int b;") in out


def test_composes_inside_variadic(tmp_path: Path) -> None:
    src = (
        "@macro V($first, $fs: variadic<tuple<$name, $type = int>>)\n"
        "@for ($name, $type) in $fs\n{{$type}} {{$name}}_{{$first}};\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "vr", "V(s, (a), (b, char))")
    assert canon("int a _ s; char b _ s;") in out


def test_composes_inside_unbounded_tuple(tmp_path: Path) -> None:
    src = (
        "@macro U($rows: tuple<tuple<$n, $t = long>...>)\n"
        "@for ($n, $t) in $rows\n{{$t}} {{$n}};\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "ub", "U(((a), (b, short)))")
    assert canon("long a; short b;") in out


def test_composes_with_named_args(tmp_path: Path) -> None:
    src = (
        "@macro N($t: tuple<$name, $type = int>, named $SUFFIX = _t)\n"
        "typedef {{$t.$type}} {{concat($t.$name, $SUFFIX)}};\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "na", "N((pt))\nN((qt, float), SUFFIX(_x))")
    assert canon("typedef int pt_t;") in out
    assert canon("typedef float qt_x;") in out


def test_composes_with_seq_index_access(tmp_path: Path) -> None:
    src = (
        "@macro I($fs: seq<tuple<$name, $type = int>>)\n"
        "first: {{$fs[0].$type}} {{$fs[0].$name}};\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "ix", "I(((a))((b, float)))")
    assert canon("first: int a;") in out


def test_composes_in_nested_loops(tmp_path: Path) -> None:
    src = (
        "@macro NN($gs: seq<seq<tuple<$n, $v = 0>>>)\n"
        "@for $g in $gs\n{\n@for ($n, $v) in $g\n{{$n}}={{$v}};\n@end\n}\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "nn", "NN((((a))((b, 7)))(((c))))")
    assert canon("{ a=0; b=7; } { c=0; }") in out
