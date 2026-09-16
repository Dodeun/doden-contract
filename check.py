#!/usr/bin/env python3
"""The tier-1 Platform Contract checker.

One implementation, two ways to run it (ADR-0010):

    # from a Project's CI - .github/workflows/contract-check.yml here
    uses: Dodeun/doden-contract/.github/workflows/contract-check.yml@v1

    # from a working tree, by a human or by a repository-scoped agent
    python3 ~/.doden-contract/check.py .

Both run this file. They are not two code paths that have to be kept in
agreement; there is one, and the workflow is a way of invoking it.

Python 3, standard library only, on purpose: a pinned checker that nothing
can update must not be able to break because of somebody else's release. The
one external dependency is `docker compose`, which the contract needs anyway
- the rendered Compose file is what gets judged, never the source text, or a
Stack whose `ports:` arrives through a variable would pass.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract.project import Project  # noqa: E402
from contract.rules import REPORTS, RULES  # noqa: E402

HERE = Path(__file__).resolve().parent


def version() -> str:
    return (HERE / "VERSION").read_text().strip()


def major(v: str) -> str:
    return v.split(".", 1)[0]


def run(tree: Path) -> dict:
    project = Project(tree, contract_root=HERE, version=version())
    violations = []
    checked = []
    for fn in RULES:
        if fn.needs_render and project.rendered is None:
            checked.append(
                {
                    "rule": fn.rule_id,
                    "summary": fn.summary,
                    "status": "not checked",
                }
            )
            continue
        found = list(fn(project))
        violations.extend(found)
        checked.append(
            {
                "rule": fn.rule_id,
                "summary": fn.summary,
                "status": "pass" if not found else "fail",
            }
        )
    for fn in REPORTS:
        for note in fn(project):
            project.note(note)

    return {
        "ok": not violations,
        "contractVersion": major(version()),
        "checkerVersion": version(),
        "tree": str(tree),
        "rules": checked,
        "violations": [v.as_dict() for v in violations],
        "notes": list(project.notes),
    }


def report(result: dict) -> str:
    lines = [
        f"doden-contract {result['checkerVersion']} "
        f"(contract {result['contractVersion']}) - tier 1",
        f"  tree: {result['tree']}",
        "",
    ]
    marks = {"pass": "PASS", "fail": "FAIL", "not checked": " -- "}
    for entry in result["rules"]:
        mark = marks[entry["status"]]
        lines.append(f"  [{mark}] {entry['rule']}: {entry['summary']}")
    if any(entry["status"] == "not checked" for entry in result["rules"]):
        lines.append("")
        lines.append(
            "  -- means the rule was not checked, not that it passed: the "
            "production Compose file did not render."
        )
    if result["notes"]:
        lines.append("")
        lines.append("Reported, not enforced:")
        lines.extend(f"  - {note}" for note in result["notes"])
    if result["violations"]:
        lines.append("")
        lines.append(f"{len(result['violations'])} violation(s):")
        for violation in result["violations"]:
            lines.append(f"  {violation['rule']}: {violation['message']}")
    lines.append("")
    lines.append("PASS: this Project satisfies tier 1." if result["ok"]
                 else "FAIL: this Project does not satisfy tier 1.")
    lines.append(
        "A green tier-1 result does not mean this Project is deployable: "
        "repository settings are tier 2, and host and account facts are "
        "outside the contract on purpose."
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py",
        description="Check a Project's working tree against tier 1 of the "
                    "Platform Contract.",
    )
    parser.add_argument(
        "tree", nargs="?", default=".", help="the Project's working tree"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the verdict as JSON"
    )
    args = parser.parse_args(argv)

    tree = Path(args.tree).resolve()
    if not tree.is_dir():
        print(f"no such directory: {tree}", file=sys.stderr)
        return 2

    result = run(tree)
    print(json.dumps(result, indent=2) if args.json else report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
