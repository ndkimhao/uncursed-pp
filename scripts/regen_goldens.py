"""Regenerate every committed golden/example header from its template.

Rewrites each `*.uncursed`'s neighbor `.h` plus any companion runtime
header the template needs (written next to the output, like the CLI).
Regeneration is MECHANICS ONLY: the repo convention still applies —
read the diff deliberately before committing (CLAUDE.md).
"""

from pathlib import Path

from uncursed_pp.emitter import compile_template

ROOTS = [Path("tests/golden"), Path("examples")]


def main() -> None:
    count = 0
    runtimes: set[Path] = set()
    for root in ROOTS:
        for template in sorted(root.rglob("*.uncursed")):
            result = compile_template(template.read_text(), template.name)
            template.with_suffix(".h").write_text(result.header)
            if result.runtime is not None:
                runtime = template.parent / result.runtime_name
                runtime.write_text(result.runtime)
                runtimes.add(runtime)
            count += 1
    print(f"regenerated {count} headers + {len(runtimes)} runtime companions")
    print("now READ the diff: git diff --stat && git diff")


if __name__ == "__main__":
    main()
