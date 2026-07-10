"""Jinja2 meta-templating: programmatic generation at uncursed-pp
compile time.

Sources pass through Jinja2 BEFORE the DSL parser, using alternative
delimiters that cannot clash with uncursed-pp's own syntax:

    statements   <<%  %>>       (for/set/if/macro/...)
    expressions  <<{  }>>
    comments     <<#  #>>

A source containing none of those markers skips Jinja2 entirely and is
returned as-is, so plain templates cannot hit meta-stage errors.
"""

from __future__ import annotations

import jinja2

from .parser import UncursedPpError

_MARKERS = ("<<%", "<<{", "<<#")

_ENV = jinja2.Environment(
    block_start_string="<<%",
    block_end_string="%>>",
    variable_start_string="<<{",
    variable_end_string="}>>",
    comment_start_string="<<#",
    comment_end_string="#>>",
    undefined=jinja2.StrictUndefined,  # typos fail loudly, never render empty
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_meta(source: str, filename: str) -> str:
    """Expand the meta-template stage of a source, or return it untouched
    when it carries no meta markers."""
    if not any(marker in source for marker in _MARKERS):
        return source
    try:
        return _ENV.from_string(source).render()
    except jinja2.TemplateSyntaxError as exc:
        raise UncursedPpError(
            f"jinja2: {exc.message}", filename, exc.lineno
        ) from exc
    except jinja2.UndefinedError as exc:
        # render-time errors: jinja rewrites the traceback so template
        # frames carry the source line
        line = 1
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == "<template>":
                line = tb.tb_lineno
            tb = tb.tb_next
        raise UncursedPpError(f"jinja2: {exc}", filename, line) from exc
