#!/usr/bin/env python3
"""The tier-2 Platform Contract audit: a Project's repository settings.

Tier 1 is checked by each Project, in its own CI, asking for no secret
(`check.py`). Tier 2 cannot be: a ruleset is not a file, so it does not
travel in a copy of a repository, and reading it needs a credential. Putting
one in every Project would destroy the property that makes tier 1 safe - a
workflow that asks for no secret cannot leak one - so this runs centrally
instead, over a list of Projects, with one token in one place.

    # against a list, with a token that may read settings and may not write
    AUDIT_TOKEN=... python3 audit.py --projects projects.json

    # against recorded settings, which is what the fixtures are - no token,
    # no network
    python3 audit.py --settings audit/fixtures/conforming.json

What it cannot do is block anything. The rulesets do that; this notices the
exception somebody added to unblock themselves and forgot, which is a thing
no check running inside the Project could ever see.

This file holds no credential and no list. The schedule, the Projects and
the token live in the private platform repository that invokes it - this
repository is public precisely because nothing in it asks for a secret, and
that stays true.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit import AuditError  # noqa: E402
from audit import report as reporting  # noqa: E402
from audit.rules import RULES, Platform  # noqa: E402
from audit.settings import Settings, fetch  # noqa: E402


def platform() -> Platform:
    """The contract as it is in this checkout - the thing Projects are held to.

    Read from the same `VERSION` and the same `CONTRACT.md` the tier-1
    checker ships, so "the version a Project pins" and "the version that is
    current" cannot be two different opinions held by two files.
    """
    version = (HERE / "VERSION").read_text().strip()
    return Platform(
        contract_version=version.split(".", 1)[0],
        contract_document=(HERE / "CONTRACT.md").read_text(encoding="utf-8"),
    )


def audit_one(settings: Settings, against: Platform) -> dict:
    checked = []
    findings = []
    for fn in RULES:
        if fn.needs is not None and not fn.needs(settings):
            checked.append(
                {"rule": fn.rule_id, "summary": fn.summary, "status": "not checked"}
            )
            continue
        found = list(fn(settings, against))
        findings.extend(found)
        checked.append(
            {
                "rule": fn.rule_id,
                "summary": fn.summary,
                "status": "pass" if not found else "fail",
            }
        )
    return {
        "repository": settings.repository,
        "ok": not findings,
        "rules": checked,
        "findings": [f.as_dict() for f in findings],
    }


def run(projects: list, against: Platform, token: str | None) -> dict:
    """Audit every Project on the list, and keep going past one that fails.

    A Project the token cannot read stops that Project and nothing else. The
    alternative - one 404 ending the run - means a repository renamed on a
    Tuesday silently stops the other Projects being audited at all.
    """
    audited = []
    unreadable = []
    for entry in projects:
        if isinstance(entry, Settings):
            audited.append(audit_one(entry, against))
            continue
        try:
            audited.append(audit_one(fetch(entry, token), against))
        except AuditError as exc:
            unreadable.append({"repository": entry, "error": str(exc)})
    return {
        "contractVersion": against.contract_version,
        "checkedAt": date.today().isoformat(),
        "ok": all(p["ok"] for p in audited) and not unreadable,
        "projects": audited,
        "unreadable": unreadable,
    }


def read_projects(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = payload["projects"] if isinstance(payload, dict) else payload
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise AuditError(f"{path} should hold a list of `owner/name` strings")
    return names


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Audit a list of Projects against tier 2 of the Platform "
                    "Contract: the settings of their repositories.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--projects",
        type=Path,
        help="a JSON list of `owner/name`, read over the API with AUDIT_TOKEN",
    )
    source.add_argument(
        "--settings",
        type=Path,
        nargs="+",
        help="recorded settings, read from disk - no token and no network",
    )
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    parser.add_argument(
        "--discord",
        action="store_true",
        help="post the verdict to DISCORD_WEBHOOK_URL as well as printing it",
    )
    parser.add_argument(
        "--message",
        action="store_true",
        help="print the Discord message instead of the report, and post "
             "nothing - how the wording is changed without posting to the "
             "channel to find out what it says",
    )
    args = parser.parse_args(argv)

    # The Discord message carries ✅ and ❌ on purpose - it is read on a
    # phone - and a console that cannot encode them must not be able to turn
    # a finished audit into a crash. Measured on Windows, where the console
    # is cp1252 by default: printing the message raised UnicodeEncodeError,
    # and in `--discord` mode it did so *after* the message had been posted,
    # so a run that did its whole job reported failure.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    try:
        against = platform()
        if args.settings:
            entries = [
                Settings.from_dict(json.loads(p.read_text(encoding="utf-8")))
                for p in args.settings
            ]
            token = None
        else:
            entries = read_projects(args.projects)
            token = os.environ.get("AUDIT_TOKEN")
            if not token:
                raise AuditError(
                    "AUDIT_TOKEN is not set. It is a token that may read "
                    "repository settings and may not write anything - the "
                    "most dangerous credential in the platform, and the "
                    "reason this audit runs in one place rather than in every "
                    "Project."
                )
        result = run(entries, against, token)
    except AuditError as exc:
        print("the audit could not run: " + str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("the audit could not run: " + str(exc), file=sys.stderr)
        return 2

    message = reporting.discord_message(result)
    if args.message:
        print(message)
    else:
        print(json.dumps(result, indent=2) if args.json else reporting.text(result))

    if args.discord:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook:
            print(
                "the audit could not report: DISCORD_WEBHOOK_URL is not set, "
                "and an audit nobody reads is a cron job",
                file=sys.stderr,
            )
            return 2
        try:
            reporting.post_to_discord(webhook, message)
        except AuditError as exc:
            print("the audit could not report: " + str(exc), file=sys.stderr)
            return 2
        # What was posted, in the run log. The webhook URL is a credential
        # and is not printed; the message is not, and a run whose log does
        # not say what it said is a run nobody can check afterwards.
        print("\nposted to Discord:\n" + message)

    # 0 conforms, 1 drifted, 2 the audit could not run - the same three the
    # checker uses, and for the same reason. A Project that could not be read
    # is 2 and never 1: "I could not look" must not arrive looking like "I
    # looked and it was wrong", and it must never arrive looking like 0.
    if result["unreadable"]:
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
