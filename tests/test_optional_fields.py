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


# ── tuple_or_token: opt-in bare-token elements ───────────────────────


def test_parse_tuple_or_token() -> None:
    src = "@macro F($t: tuple_or_token<$n, $v?>)\n{{$t.$n}}\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    t = macro.params[0].type
    assert t == TupleT(("n", "v"), (None, None), (1,), or_token=True)


def test_tuple_or_token_needs_single_required_field() -> None:
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(
            "@macro F($t: tuple_or_token<$a, $b, $c?>)\nx\n@endmacro\n", "t.uncursed"
        )
    assert "one required field" in str(excinfo.value)


def test_define_struct_with_bare_mixed_elements(tmp_path: Path) -> None:
    # the motivating example: DEFINE_STRUCT(Foo, a, b, (c, 1), d)
    src = (
        "@macro DEFINE_STRUCT($name, $ms: variadic<tuple_or_token<$m, $init?>>)\n"
        'struct {{$name}} { @join $ms as $f with " ": '
        "@if has($f.$init) @then {{$f.$m}} = {{$f.$init}}, @else {{$f.$m}}, @end@end };\n"
        "@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "ds", "DEFINE_STRUCT(Foo, a, b, (c, 1), d)")
    assert canon("struct Foo { a, b, c = 1, d, };") in out


def test_define_enum_with_unbounded_tuple_elements(tmp_path: Path) -> None:
    # the second motivating example: DEFINE_ENUM(E, (x, (y, 1), z))
    src = (
        "@macro DEFINE_ENUM($name, $es: tuple<tuple_or_token<$n, $v?>...>)\n"
        'enum {{$name}} { @join $es as $e with " ": '
        "@if has($e.$v) @then {{$e.$n}} = {{$e.$v}}, @else {{$e.$n}}, @end@end };\n"
        "@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "de", "DEFINE_ENUM(E, (x, (y, 1), z))")
    assert canon("enum E { x, y = 1, z, };") in out


def test_tuple_or_token_as_direct_param(tmp_path: Path) -> None:
    src = (
        "@macro P($t: tuple_or_token<$n, $v = 0>)\np({{$t.$n}}, {{$t.$v}});\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "dp", "P(bare)\nP((x, 9))")
    assert canon("p(bare, 0);") in out
    assert canon("p(x, 9);") in out


def test_tuple_or_token_in_seq(tmp_path: Path) -> None:
    src = (
        "@macro S($es: seq<tuple_or_token<$n, $v = 0>>)\n"
        "@for ($n, $v) in $es\ns({{$n}}, {{$v}});\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "sq", "S((a)((b, 2)))")
    assert canon("s(a, 0); s(b, 2);") in out


def test_plain_optional_tuples_have_no_probe() -> None:
    # the probe is OPT-IN: plain tuple<...> shapes must not pay for it
    src = "@macro F($t: tuple<$n, $v = 0>)\n{{$t.$n}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "_R_" not in out and "_CT" not in out


# ── typed 'named variadic': iterable keyword lists ───────────────────

DS = (
    "@macro DEFINE_STRUCT($name,\n"
    "    named variadic $MEMBERS: tuple_or_token<$m, $init?> = ,\n"
    "    named variadic $FUNCS: token = )\n"
    'struct {{$name}} { @join $MEMBERS as $f with " ": '
    "@if has($f.$init) @then {{$f.$m}} = {{$f.$init}}, @else {{$f.$m}}, @end@end"
    ' @join $FUNCS as $g with " ": {{$g}}(), @end };\n'
    "@endmacro\n"
)


def test_named_variadic_with_element_type(tmp_path: Path) -> None:
    # the motivating example, keyword-list form
    out = preprocess_src(
        tmp_path, DS, "kv", "DEFINE_STRUCT(Foo, MEMBERS(a, b, (c, 1), d), FUNCS(q, w))"
    )
    assert canon("struct Foo { a, b, c = 1, d, q(), w(), };") in out


def test_typed_keyword_lists_are_optional_and_ordered_freely(tmp_path: Path) -> None:
    out = preprocess_src(
        tmp_path, DS, "kv2", "DEFINE_STRUCT(Bar, FUNCS(q))\nDEFINE_STRUCT(Baz)"
    )
    assert canon("struct Bar { q(), };") in out
    assert canon("struct Baz { };") in out


def test_named_variadic_elem_type_must_be_element_like() -> None:
    with pytest.raises(UncursedPpError):
        parse_file(
            "@macro F($a, named variadic $XS: variadic = )\nx\n@endmacro\n",
            "t.uncursed",
        )


# ── seq_or_token: bare token promotes to a 1-element seq ─────────────


def test_parse_seq_or_token() -> None:
    from uncursed_pp.nodes import SeqT, TokenT

    src = "@macro F($xs: seq_or_token<token>)\n{{len($xs)}}\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    assert macro.params[0].type == SeqT(TokenT(), or_token=True)


def test_seq_or_token_param(tmp_path: Path) -> None:
    src = (
        "@macro CALLS($xs: seq_or_token<token>)\n"
        "@for $x in $xs\nf({{$x}});\n@end\nn={{len($xs)}}\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "st", "CALLS(one)\nCALLS((a)(b))")
    assert canon("f(one); n=1") in out
    assert canon("f(a); f(b); n=2") in out


def test_seq_or_token_variadic_elements(tmp_path: Path) -> None:
    src = (
        "@macro GROUPS($gs: variadic<seq_or_token<token>>)\n"
        "@for $g in $gs\n[\n@for $x in $g\n{{$x}};\n@end\n]\n@end\n@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "sg", "GROUPS(solo, (a)(b))")
    assert canon("[ solo; ] [ a; b; ]") in out


def test_seq_or_token_in_typed_keyword_list(tmp_path: Path) -> None:
    src = (
        "@macro W($n, named variadic $GROUPS: seq_or_token<token> = )\n"
        '@for $g in $GROUPS\n{ @join $g as $x with ",": {{$x}}@end }\n@end\n@endmacro\n'

    )
    out = preprocess_src(tmp_path, src, "sk", "W(w, GROUPS(solo, (a)(b)))")
    assert canon("{ solo } { a,b }") in out


def test_plain_seq_has_no_probe() -> None:
    src = "@macro F($xs: seq<token>)\n{{len($xs)}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "_NS" not in out and "_R_" not in out


def test_or_token_types_past_the_chain_limit(tmp_path: Path) -> None:
    # >16 elements takes the SEQ_FOR_EACH fallback (BIG) instead of the
    # consumption chain: normalization must ride both paths
    src = (
        "@macro TL($ms: variadic<tuple_or_token<$m, $v = 0>>)\n"
        "@for ($m, $v) in $ms\nt({{$m}}, {{$v}});\n@end\n@endmacro\n"
        "@macro SL($xs: seq_or_token<token>)\n"
        "@for $x in $xs\ns({{$x}});\n@end\nn={{len($xs)}}\n@endmacro\n"
    )
    elems = ", ".join(f"(m{i}, {i})" if i % 2 else f"m{i}" for i in range(20))
    out = preprocess_src(tmp_path, src, "big", f"TL({elems})")
    assert canon("t(m0, 0);") in out and canon("t(m19, 19);") in out
    assert canon("t(m1, 1);") in out  # tuple element deep in the tail
    seq = "".join(f"(s{i})" for i in range(20))
    out2 = preprocess_src(tmp_path, src, "big2", f"SL({seq})\nSL(solo)")
    assert canon("s(s0);") in out2 and canon("s(s19);") in out2
    assert canon("n=20") in out2 and canon("n=1") in out2


def test_typed_keyword_list_past_the_chain_limit(tmp_path: Path) -> None:
    src = (
        "@macro KL($n, named variadic $MS: tuple_or_token<$m, $v?> = )\n"
        "@for ($m, $v) in $MS\nk({{$m}});\n@end\n@endmacro\n"
    )
    elems = ", ".join(f"(k{i}, {i})" if i % 3 == 0 else f"k{i}" for i in range(20))
    out = preprocess_src(tmp_path, src, "kbig", f"KL(x, MS({elems}))")
    assert canon("k(k0);") in out and canon("k(k19);") in out
