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
from audit.rules import APP_RULES, PROJECTS_APP_RULES, RULES, Platform  # noqa: E402
from audit.settings import AppSettings, Settings, fetch, fetch_app  # noqa: E402


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
        # The Add-ons that have a document *are* the ones with a document:
        # this directory is the list, so a new Add-on's document is audited
        # from the moment it is written, with nothing to register anywhere.
        addon_documents={
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted((HERE / "docs" / "addons").glob("*.md"))
        },
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


def audit_app(app: AppSettings, projects, rules=APP_RULES) -> dict:
    """An App's verdict, in the shape of a Project's."""
    checked = []
    findings = []
    for fn in rules:
        found = list(fn(app, projects))
        findings.extend(found)
        checked.append(
            {"rule": fn.rule_id, "summary": fn.summary, "status": "pass" if not found else "fail"}
        )
    return {
        "app": app.slug,
        "ok": not findings,
        "rules": checked,
        "findings": [f.as_dict() for f in findings],
    }


def agent(app: AppSettings, projects: list) -> dict:
    """Which Projects on the list the agent can reach, and which it cannot.

    Decision 83 of phase 6: every Project has the agent unless `--no-agent`
    declined it, and adding or removing one later is a click in GitHub, so
    every report says where it stands. Matched without case, as GitHub's
    names are.
    """
    held = {name.lower() for name in app.repositories}
    return {
        "app": app.slug,
        "with": [p for p in projects if p.lower() in held],
        "without": [p for p in projects if p.lower() not in held],
    }


def read_one_app(entry, key: str | None, key_name: str, topics: bool, unreadable: list):
    """One App: recorded, or read with its key. Returns its settings, or None.

    An App that cannot be read - no key, a wrong one, an installation gone -
    stops that App and nothing else, like a Project, and is added to
    `unreadable`.
    """
    if isinstance(entry, AppSettings):
        return entry
    if not key:
        unreadable.append({
            "app": entry["slug"],
            "error": f"{key_name} is not set. No credential short of the "
                     "App's own key can read a private App, so it was not "
                     "audited at all.",
        })
        return None
    try:
        return fetch_app(entry, key, key_name=key_name, topics=topics)
    except AuditError as exc:
        unreadable.append({"app": entry["slug"], "error": str(exc)})
        return None


def run(
    projects: list,
    against: Platform,
    token: str | None,
    app: "AppSettings | dict | None" = None,
    app_key: str | None = None,
    projects_app: "AppSettings | dict | None" = None,
    projects_app_key: str | None = None,
) -> dict:
    """Audit every Project on the list, and keep going past one that fails.

    A Project the token cannot read stops that Project and nothing else. The
    alternative - one 404 ending the run - means a repository renamed on a
    Tuesday silently stops the other Projects being audited at all.

    `app` is the Prototypes' App: recorded `AppSettings`, or the list's entry
    for it, read with `app_key`. `projects_app` is the Projects' App, the
    agent's (ADR-0017), the same way with `projects_app_key`. Each is judged
    against every Project on the list, whether or not that Project could be
    read. An App that cannot be read - no key, a wrong one, an installation
    gone - stops the App and nothing else, like a Project: the Projects are
    still reported.

    With the Projects' App on the list, the verdict carries `agent`: the
    Projects with the agent and those without, or `unknown` when the App
    could not be read.
    """
    audited = []
    unreadable = []
    # Every Project on the list, by name, to what was read of it: None when it
    # could not be read. The Projects' App's rules need the Manifests.
    read = {}
    for entry in projects:
        name = entry.repository if isinstance(entry, Settings) else entry
        try:
            settings = entry if isinstance(entry, Settings) else fetch(entry, token)
        except AuditError as exc:
            unreadable.append({"repository": entry, "error": str(exc)})
            read[name] = None
            continue
        audited.append(audit_one(settings, against))
        read[name] = settings
    names = list(read)

    apps = []
    if app is not None:
        found = read_one_app(app, app_key, "APP_PRIVATE_KEY", False, unreadable)
        if found is not None:
            apps.append(audit_app(found, names))
    reach = None
    if projects_app is not None:
        found = read_one_app(
            projects_app, projects_app_key, "PROJECTS_APP_PRIVATE_KEY", True, unreadable
        )
        if found is not None:
            apps.append(audit_app(found, read, PROJECTS_APP_RULES))
            reach = agent(found, names)
        else:
            reach = {"app": projects_app["slug"], "unknown": True}
    result = {
        "contractVersion": against.contract_version,
        "checkedAt": date.today().isoformat(),
        "ok": all(p["ok"] for p in audited + apps) and not unreadable,
        "projects": audited,
        "apps": apps,
        "unreadable": unreadable,
    }
    if reach is not None:
        result["agent"] = reach
    return result


def read_json(path: Path):
    """Read a JSON file, or say which file could not be read.

    Every one of these is a setup mistake rather than a verdict about a
    Project, so each becomes exit 2. A JSONDecodeError left to escape is a
    traceback, and a traceback exits 1 - the status that means "drifted",
    which is the one thing it must never be mistaken for.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuditError(f"{path} is not valid JSON: {exc}") from exc


def read_projects(path: Path) -> list:
    payload = read_json(path)
    names = payload.get("projects") if isinstance(payload, dict) else payload
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise AuditError(
            f"{path} should hold a list of `owner/name` strings, under a "
            "`projects` key or on its own"
        )
    return names


def read_app(path: Path, key: str = "app") -> dict | None:
    """The list's entry for an App, or None if it names none.

    `app` is the Prototypes' App, `projectsApp` the Projects'. `slug` names
    it in the report, `appId` signs its JWT and `installationId` is the one
    installation whose repositories are read. None of the three is a
    secret. The key is, and it is not here.
    """
    payload = read_json(path)
    app = payload.get(key) if isinstance(payload, dict) else None
    if app is None:
        return None
    if not (
        isinstance(app, dict)
        and isinstance(app.get("slug"), str)
        and isinstance(app.get("appId"), int)
        and isinstance(app.get("installationId"), int)
    ):
        raise AuditError(
            f"{path}: `{key}` should be an object with a `slug` string and the "
            "`appId` and `installationId` numbers"
        )
    return app


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
    parser.add_argument(
        "--app-settings",
        type=Path,
        help="the Prototypes' App, recorded, judged with --settings - no key "
             "and no network",
    )
    parser.add_argument(
        "--projects-app-settings",
        type=Path,
        help="the Projects' App, the agent's, recorded, judged with --settings "
             "- no key and no network",
    )
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    # Mutually exclusive rather than merely documented: `--message` promises
    # to post nothing, and a promise a flag combination can break is not one.
    reporting_mode = parser.add_mutually_exclusive_group()
    reporting_mode.add_argument(
        "--discord",
        action="store_true",
        help="post the verdict to DISCORD_WEBHOOK_URL as well as printing it",
    )
    reporting_mode.add_argument(
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

    if (args.app_settings or args.projects_app_settings) and not args.settings:
        parser.error("--app-settings and --projects-app-settings are recorded, "
                     "and go with --settings")

    try:
        against = platform()
        app, app_key = None, None
        projects_app, projects_app_key = None, None
        if args.settings:
            entries = [Settings.from_dict(read_json(p)) for p in args.settings]
            token = None
            if args.app_settings:
                app = AppSettings.from_dict(read_json(args.app_settings))
            if args.projects_app_settings:
                projects_app = AppSettings.from_dict(read_json(args.projects_app_settings))
        else:
            entries = read_projects(args.projects)
            app = read_app(args.projects)
            projects_app = read_app(args.projects, "projectsApp")
            token = os.environ.get("AUDIT_TOKEN")
            if not token:
                raise AuditError(
                    "AUDIT_TOKEN is not set. It is a token that may read "
                    "repository settings and may not write anything - the "
                    "most dangerous credential in the platform, and the "
                    "reason this audit runs in one place rather than in every "
                    "Project."
                )
            app_key = os.environ.get("APP_PRIVATE_KEY")
            projects_app_key = os.environ.get("PROJECTS_APP_PRIVATE_KEY")
        result = run(
            entries, against, token, app, app_key, projects_app, projects_app_key
        )
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
