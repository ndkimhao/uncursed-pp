"""Helper dedup/factoring pass.

Runs over all macros' generated helpers before the header is assembled:

1. Exact merge: helpers with identical (params, body) collapse into one
   shared `<prefix>H<n>` definition (repeated until no change, so helpers
   that only differed in the names of already-merged sub-helpers merge too).
2. Parameterized merge: loop helpers (the only kind with the spare `d`
   FOR_EACH data slot) whose bodies differ in exactly ONE token collapse
   into one shared helper reading that token from `d`. Anything needing
   more machinery (several differing tokens) stays unmerged on purpose.

Shared helpers are named <prefix><FILESTEM>_H<n> in first-use order —
deterministic, and distinct across independently generated headers so two
headers in one translation unit cannot collide. Only helpers with 2+ users
ever merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .emitter import C_LITERAL_PATTERN, _Helper, _MacroOut

# String/char literals are single opaque tokens: a merge hole must never
# open inside one (macro params don't substitute there), but a WHOLE
# literal may ride the d slot.
_TOKEN_RE = re.compile(C_LITERAL_PATTERN + r"|\w+|[^\w\s]")

# Tokens that can never ride the d slot: splicing them into the
# FOR_EACH call's argument list would unbalance or re-split it.
_UNSPLICEABLE = {"(", ")", ","}

# Chain members are referenced by CAT-assembled *prefix* (SMALL does
# BOOST_PP_CAT(<family>, size)), a name the \b<whole-name>\b rename can
# never see - so they must merge as whole families, never individually.
_CHAIN_MEMBER_RE = re.compile(r"^(?P<family>.+_(?:CH|HC)\d+_)(?P<idx>\d+)$")


@dataclass
class _Site:
    out: _MacroOut
    helper: _Helper


def collapse(outs: list[_MacroOut], shared_prefix: str) -> None:
    counter = _SharedCounter(shared_prefix)
    while _merge_exact(outs, counter):
        pass
    _merge_chain_families(outs, counter)
    _merge_parameterized(outs, counter)


class _SharedCounter:
    def __init__(self, shared_prefix: str):
        self.shared_prefix = shared_prefix
        self.n = 0

    def next_name(self) -> str:
        self.n += 1
        return f"{self.shared_prefix}H{self.n}"

    def next_family(self) -> str:
        self.n += 1
        return f"{self.shared_prefix}HC{self.n}_"


def _sites(outs: list[_MacroOut]) -> list[_Site]:
    return [_Site(out, h) for out in outs for h in out.helpers]


def _rename(outs: list[_MacroOut], old: str, new: str) -> None:
    pattern = re.compile(rf"\b{re.escape(old)}\b")
    for out in outs:
        for helper in out.helpers:
            helper.body = pattern.sub(new, helper.body)
        out.defines = [pattern.sub(new, d) for d in out.defines]


def _rewrite_data_arg(outs: list[_MacroOut], old: str, new: str, const: str) -> None:
    """Replace a `(OLD, ~, ...)` loop call site with `(NEW, const, ...)`."""
    pattern = re.compile(rf"\b{re.escape(old)}\b, ~, ")
    replacement = f"{new}, {const}, "
    for out in outs:
        for helper in out.helpers:
            helper.body = pattern.sub(replacement, helper.body)
        out.defines = [pattern.sub(replacement, d) for d in out.defines]


def _drop(site: _Site) -> None:
    site.out.helpers.remove(site.helper)


def _merge_exact(outs: list[_MacroOut], counter: _SharedCounter) -> bool:
    groups: dict[tuple[str, str], list[_Site]] = {}
    for site in _sites(outs):
        h = site.helper
        if _CHAIN_MEMBER_RE.match(h.name):
            continue  # families merge as units in _merge_chain_families
        groups.setdefault((h.params, h.body), []).append(site)

    merged = False
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = members[0].helper
        if _is_shared(canonical.name, counter.shared_prefix):
            new_name = canonical.name  # already shared; absorb the extra copies
        else:
            new_name = counter.next_name()
            _rename(outs, canonical.name, new_name)
            canonical.name = new_name
        for site in members[1:]:
            _rename(outs, site.helper.name, new_name)
            _drop(site)
        merged = True
        break  # indices shifted; restart scan
    return merged


def _is_shared(name: str, shared_prefix: str) -> bool:
    return re.fullmatch(rf"{re.escape(shared_prefix)}H\d+", name) is not None


def _rename_family_prefix(outs: list[_MacroOut], old: str, new: str) -> None:
    """Rewrite a chain-family prefix everywhere it appears - as the stem of
    member names (digits follow) AND as the bare CAT-assembled reference in
    the dispatch (a non-word character follows). The lookahead keeps the
    match away from LONGER identifiers that merely start with the prefix:
    a user macro named A_CH1 owns machinery like UNCURSED_PP_A_CH1_EACH1,
    which merging macro A's UNCURSED_PP_A_CH1_ family must not touch."""
    pattern = re.compile(rf"\b{re.escape(old)}(?=\d+\b|\W|$)")
    for out in outs:
        for helper in out.helpers:
            helper.body = pattern.sub(new, helper.body)
        out.defines = [pattern.sub(new, d) for d in out.defines]


def _merge_chain_families(outs: list[_MacroOut], counter: _SharedCounter) -> None:
    families: dict[str, list[_Site]] = {}
    for site in _sites(outs):
        m = _CHAIN_MEMBER_RE.match(site.helper.name)
        if m:
            families.setdefault(m.group("family"), []).append(site)

    by_signature: dict[tuple[tuple[str, str], ...], list[str]] = {}
    for family, sites in families.items():
        sites.sort(key=lambda s: int(s.helper.name[len(family):]))
        signature = tuple(
            (s.helper.params, s.helper.body.replace(family, "\x00")) for s in sites
        )
        by_signature.setdefault(signature, []).append(family)

    for family_list in by_signature.values():
        if len(family_list) < 2:
            continue
        shared = counter.next_family()
        canonical = family_list[0]
        _rename_family_prefix(outs, canonical, shared)
        for site in families[canonical]:
            site.helper.name = shared + site.helper.name[len(canonical):]
        for family in family_list[1:]:
            _rename_family_prefix(outs, family, shared)
            for site in families[family]:
                _drop(site)


def _merge_parameterized(outs: list[_MacroOut], counter: _SharedCounter) -> None:
    # only helpers that OWN a spare d slot (loop EACH helpers, marked at
    # creation) qualify: an @if branch helper whose user-named params
    # happen to spell "r, d, e" has no slot to parameterize through, and
    # its call sites don't have the FOR_EACH shape _rewrite_data_arg edits
    candidates = [
        site
        for site in _sites(outs)
        if site.helper.data_param is not None
        and not _is_shared(site.helper.name, counter.shared_prefix)
    ]
    used: set[int] = set()
    clusters: list[list[_Site]] = []
    for i, a in enumerate(candidates):
        if i in used:
            continue
        cluster = [a]
        hole = None
        for j in range(i + 1, len(candidates)):
            if j in used:
                continue
            b = candidates[j]
            if b.helper.params != a.helper.params:
                continue
            diff = _single_diff(a.helper.body, b.helper.body)
            if diff is None:
                continue
            if hole is None:
                hole = diff
            elif diff != hole:
                continue
            cluster.append(b)
            used.add(j)
        if len(cluster) > 1 and hole is not None:
            used.add(i)
            clusters.append(cluster)

    for cluster in clusters:
        canonical = cluster[0].helper
        d_param = canonical.data_param
        assert d_param is not None
        hole = _single_diff(canonical.body, cluster[1].helper.body)
        assert hole is not None
        spans = _token_spans(canonical.body)
        param_tokens = set(_TOKEN_RE.findall(canonical.params))
        consts = [_token_spans(s.helper.body)[hole][0] for s in cluster]
        if any(c in param_tokens or c in _UNSPLICEABLE for c in consts):
            continue  # differing token is loop machinery or unspliceable
        if any(d_param in _tokens(s.helper.body) for s in cluster):
            continue  # body already uses the data slot

        new_name = counter.next_name()
        start, end = spans[hole][1], spans[hole][2]
        before, after = canonical.body[:start], canonical.body[end:]
        # keep the spliced d a standalone token: pad when flush against
        # a word character on either side
        mid = d_param
        if before and (before[-1].isalnum() or before[-1] == "_"):
            mid = " " + mid
        if after and (after[0].isalnum() or after[0] == "_"):
            mid = mid + " "
        shared_body = before + mid + after
        for site in cluster:
            _rewrite_data_arg(outs, site.helper.name, new_name, _token_spans(site.helper.body)[hole][0])
        canonical.body = shared_body
        canonical.name = new_name
        for site in cluster[1:]:
            _drop(site)


def _tokens(text: str) -> list[str]:
    # finditer + group(0): findall would return the literal pattern's
    # group captures instead of whole matches
    return [m.group(0) for m in _TOKEN_RE.finditer(text)]


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _single_diff(a: str, b: str) -> int | None:
    """Index of the single differing token, or None if 0 or 2+ differ."""
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) != len(tb):
        return None
    diffs = [i for i, (x, y) in enumerate(zip(ta, tb)) if x != y]
    return diffs[0] if len(diffs) == 1 else None
