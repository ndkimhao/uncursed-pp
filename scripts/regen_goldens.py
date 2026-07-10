"""Regenerate every committed golden/example header from its template.

Rewrites each `*.uncursed`'s neighbor `.h` plus the companion runtime
headers. Companions are shared per directory, so their chain-limit
tables are the UNION of every sharing template's needs — a lone
default-limit template must not clobber a neighbor's LE<K> table.
Regeneration is MECHANICS ONLY: the repo convention still applies —
read the diff deliberately before committing (CLAUDE.md).
"""

from pathlib import Path

from uncursed_pp.emitter import EmitConfig, compile_template, runtime_header

ROOTS = [Path("tests/golden"), Path("examples")]


def main() -> None:
    count = 0
    # (directory, runtime filename) -> (resolved config, union of LE<K> tables)
    companions: dict[tuple[Path, str], tuple[EmitConfig, set[int]]] = {}
    for root in ROOTS:
        for template in sorted(root.rglob("*.uncursed")):
            result = compile_template(template.read_text(), template.name)
            template.with_suffix(".h").write_text(result.header)
            if result.runtime is not None:
                key = (template.parent, result.runtime_name)
                _, limits = companions.setdefault(key, (result.config, set()))
                limits.update(result.chain_limits)
            count += 1
    for (directory, name), (config, limits) in companions.items():
        (directory / name).write_text(
            runtime_header(config, chain_limits=sorted(limits | {16}))
        )
    print(f"regenerated {count} headers + {len(companions)} runtime companions")
    print("now READ the diff: git diff --stat && git diff")


if __name__ == "__main__":
    main()
