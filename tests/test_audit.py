"""Every tier-2 rule, demonstrated on settings that violate it.

The seam is deliberate and it is the whole reason this is testable: reading a
repository's settings is one file (`audit/settings.py`), and everything past
it takes a record and returns findings. So a rule can be exercised against
recorded settings instead of against a live repository - and a rule that can
only be exercised by breaking a real Project's configuration is a rule
nobody will exercise twice.

Fixtures are overlays on `audit/fixtures/conforming.json`, recorded from this
platform's first Project and trimmed to the fields the audit reads. A failing
fixture ships only the difference, so the fixture *is* the edit that makes
the rule fire - and it carries the rules it must fire and why, beside the
edit rather than in a table somewhere else.

Tests run `audit.py` as a subprocess, which is the command the scheduled
workflow runs and the command a human runs. The audit is copied into a
temporary directory first, with a stub `CONTRACT.md` and `VERSION`, because
two of the rules judge a Project against *this* checkout's document and
version: a fixture carrying the real 15 kB document would have to be rewritten
every time a sentence of it changes.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "audit" / "fixtures"
CANONICAL_DOCUMENT = "the canonical CONTRACT.md\n"
CANONICAL_ADDON_DOCUMENT = "the canonical database Add-on document\n"
STUB_VERSION = "v3.0.0"


def install() -> Path:
    """A copy of the audit with a stub contract, so fixtures stay small."""
    home = Path(tempfile.mkdtemp(prefix="doden-audit-"))
    shutil.copy(ROOT / "audit.py", home / "audit.py")
    shutil.copytree(ROOT / "audit", home / "audit")
    (home / "VERSION").write_text(STUB_VERSION)
    (home / "CONTRACT.md").write_text(CANONICAL_DOCUMENT, encoding="utf-8")
    # Stubbed for the same reason as CONTRACT.md: the rule compares a
    # Project's copy against *this* checkout's, and a fixture carrying the
    # real document would be rewritten every time a sentence of it changes.
    # The directory is the list of Add-ons that have a document, so one file
    # here is one documented Add-on in the fixture world.
    addons = home / "docs" / "addons"
    addons.mkdir(parents=True)
    (addons / "database.md").write_text(CANONICAL_ADDON_DOCUMENT, encoding="utf-8")
    return home


def merge(overlay: dict) -> dict:
    """Apply a fixture overlay to the conforming settings.

    `rulesets` is keyed by name: a patch updates that ruleset's keys, `null`
    removes it, and a key prefixed with `+` adds one. `settings` replaces
    top-level fields, merging one level into a dict so that a fixture can
    change a single Manifest field without restating the Manifest.
    """
    settings = json.loads((FIXTURES / "conforming.json").read_text(encoding="utf-8"))
    for name, patch in (overlay.get("rulesets") or {}).items():
        if name.startswith("+"):
            settings["rulesets"].append(patch)
            continue
        matching = [rs for rs in settings["rulesets"] if rs["name"] == name]
        if not matching:
            raise AssertionError(f"no ruleset called {name} in the conforming fixture")
        if patch is None:
            settings["rulesets"].remove(matching[0])
        else:
            matching[0].update(patch)
    for field, value in (overlay.get("settings") or {}).items():
        if isinstance(value, dict) and isinstance(settings.get(field), dict):
            settings[field].update(value)
        else:
            settings[field] = value
    return settings


class AuditTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = install()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def audit(self, *settings: dict):
        """Run audit.py over recorded settings. Returns (verdict, exit status)."""
        directory = Path(tempfile.mkdtemp(prefix="doden-settings-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        paths = []
        for index, payload in enumerate(settings):
            path = directory / f"{index}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(str(path))
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--json", "--settings", *paths],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 2:
            return proc.stderr, 2
        return json.loads(proc.stdout), proc.returncode


class TheConformingFixture(AuditTestCase):
    def test_every_rule_passes(self):
        result, status = self.audit(merge({}))
        self.assertEqual(0, status, result)
        project = result["projects"][0]
        self.assertEqual([], project["findings"])
        for entry in project["rules"]:
            self.assertEqual("pass", entry["status"], entry["rule"])

    def test_it_is_the_settings_of_a_real_project(self):
        """The base is recorded, not invented.

        Every rule is judged against a payload of the shape GitHub actually
        returns, so a rule cannot pass because a fixture was written to the
        rule's own idea of the schema.
        """
        settings = merge({})
        names = {rs["name"] for rs in settings["rulesets"]}
        self.assertEqual({"protect-main", "protect-releases"}, names)


class EveryRuleFires(AuditTestCase):
    """A rule with no failing example is a rule that has never run."""

    def test_each_failing_fixture_fires_exactly_what_it_claims(self):
        for path in sorted((FIXTURES / "fail").glob("*.json")):
            with self.subTest(fixture=path.name):
                overlay = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    overlay.get("why"),
                    "a fixture states why the thing it does is dangerous",
                )
                result, status = self.audit(merge(overlay))
                self.assertEqual(1, status, result)
                fired = {f["rule"] for f in result["projects"][0]["findings"]}
                self.assertEqual(set(overlay["fires"]), fired)

    def test_each_passing_fixture_comes_back_clean(self):
        for path in sorted((FIXTURES / "pass").glob("*.json")):
            with self.subTest(fixture=path.name):
                overlay = json.loads(path.read_text(encoding="utf-8"))
                result, status = self.audit(merge(overlay))
                self.assertEqual(0, status, result)
                project = result["projects"][0]
                self.assertEqual([], project["findings"])
                unchecked = {
                    e["rule"] for e in project["rules"] if e["status"] == "not checked"
                }
                self.assertEqual(set(overlay.get("not_checked", [])), unchecked)

    def test_every_rule_has_a_failing_fixture(self):
        result, _ = self.audit(merge({}))
        rules = {entry["rule"] for entry in result["projects"][0]["rules"]}
        covered = set()
        for path in (FIXTURES / "fail").glob("*.json"):
            covered |= set(json.loads(path.read_text(encoding="utf-8"))["fires"])
        self.assertEqual(rules, covered)

    def test_a_finding_names_what_to_fix(self):
        for path in sorted((FIXTURES / "fail").glob("*.json")):
            with self.subTest(fixture=path.name):
                overlay = json.loads(path.read_text(encoding="utf-8"))
                result, _ = self.audit(merge(overlay))
                for finding in result["projects"][0]["findings"]:
                    self.assertGreater(
                        len(finding["message"]), 40, "a verdict is not a message"
                    )
                    self.assertTrue(
                        finding["message"].rstrip().endswith("."),
                        "a finding is a sentence somebody has to act on",
                    )


class NotCheckedIsNotPassed(AuditTestCase):
    """The distinction the report exists to keep.

    An audit that cannot see a setting must not report it in the Project's
    favour, because the whole value of the thing is that somebody trusts it
    when it is quiet.
    """

    def test_unreadable_variables_are_reported_as_unchecked(self):
        result, status = self.audit(merge({"settings": {"variables": None}}))
        self.assertEqual(0, status)
        statuses = {
            e["rule"]: e["status"] for e in result["projects"][0]["rules"]
        }
        self.assertEqual("not checked", statuses["no-repository-variables"])

    def test_a_ruleset_matching_twice_is_reported_once(self):
        """A condition can include `~DEFAULT_BRANCH` *and* the literal branch.

        The ruleset then answers to two of the audit's questions about
        coverage, and without care its one empty-bypass failure is reported
        twice - two lines in Discord about one setting, which is how a report
        starts being skimmed.
        """
        result, status = self.audit(
            merge(
                {
                    "rulesets": {
                        "protect-main": {
                            "conditions": {
                                "ref_name": {
                                    "include": ["~DEFAULT_BRANCH", "refs/heads/main"],
                                    "exclude": [],
                                }
                            },
                            "bypass_actors": [
                                {"actor_id": 5, "actor_type": "RepositoryRole"}
                            ],
                        }
                    }
                }
            )
        )
        self.assertEqual(1, status)
        bypass = [
            f
            for f in result["projects"][0]["findings"]
            if "bypass list" in f["message"]
        ]
        self.assertEqual(1, len(bypass), bypass)

    def test_the_report_says_what_not_checked_means(self):
        directory = Path(tempfile.mkdtemp(prefix="doden-settings-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "s.json"
        path.write_text(json.dumps(merge({"settings": {"variables": None}})))
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--settings", str(path)],
            capture_output=True,
            text=True,
        )
        self.assertIn("not that it passed", proc.stdout)


class WhatGitHubApplies(AuditTestCase):
    """The rulesets say `active`; GitHub is what says whether they apply.

    Decision 31 of phase 4: the production rulesets rest on a free GitHub Pro
    that will lapse, and the likeliest failure is a ruleset still listed as
    `active` that refuses nothing.
    """

    def findings(self, overlay):
        result, status = self.audit(merge(overlay))
        return status, [
            f for f in result["projects"][0]["findings"] if f["rule"] == "rules-apply"
        ]

    def test_the_lapsed_case_names_everything_that_no_longer_applies(self):
        overlay = json.loads(
            (FIXTURES / "fail" / "rules-apply-lapsed.json").read_text(encoding="utf-8")
        )
        status, findings = self.findings(overlay)
        self.assertEqual(1, status)
        self.assertEqual(1, len(findings), findings)
        message = findings[0]["message"]
        for name in ("pull_request", "deletion", "non_fast_forward", "required_status_checks"):
            self.assertIn(f"`{name}`", message)
        self.assertIn("`protect-main`", message)
        self.assertIn("tag", message)

    def test_a_partial_loss_names_only_what_was_lost(self):
        overlay = json.loads(
            (FIXTURES / "fail" / "rules-apply-partial.json").read_text(encoding="utf-8")
        )
        status, findings = self.findings(overlay)
        self.assertEqual(1, status)
        self.assertEqual(1, len(findings), findings)
        message = findings[0]["message"]
        self.assertIn("`pull_request`", message)
        self.assertNotIn("`deletion`", message)

    def test_a_rule_the_rulesets_never_declared_is_not_reported_twice(self):
        """Missing from the ruleset is `protect-main`'s finding, not this one's."""
        status, findings = self.findings(
            {
                "rulesets": {
                    "protect-main": {
                        "rules": [
                            {"type": "deletion"},
                            {"type": "non_fast_forward"},
                            {
                                "type": "required_status_checks",
                                "parameters": {
                                    "strict_required_status_checks_policy": True,
                                    "required_status_checks": [
                                        {"context": "test"},
                                        {"context": "contract / tier-1"},
                                    ],
                                },
                            },
                        ]
                    }
                },
                "settings": {
                    "applied_branch_rules": [
                        {"type": "deletion"},
                        {"type": "non_fast_forward"},
                        {"type": "required_status_checks"},
                    ]
                },
            }
        )
        self.assertEqual(1, status)
        self.assertEqual([], findings)

    def test_rules_applied_from_any_source_count(self):
        """Types are compared, never which ruleset produced them, so a rule
        applied by an organisation ruleset - or recorded without its source -
        is as applied as any other."""
        status, findings = self.findings(
            {
                "settings": {
                    "applied_branch_rules": [
                        {"type": t}
                        for t in (
                            "deletion",
                            "non_fast_forward",
                            "pull_request",
                            "required_status_checks",
                        )
                    ]
                }
            }
        )
        self.assertEqual(0, status)
        self.assertEqual([], findings)


class TheAuditFailingIsNotTheProjectFailing(AuditTestCase):
    """Exit 2 means "could not run", and must never read as exit 1 or 0."""

    def test_a_missing_token_is_exit_2(self):
        projects = Path(tempfile.mkdtemp(prefix="doden-projects-"))
        self.addCleanup(shutil.rmtree, projects, ignore_errors=True)
        listing = projects / "projects.json"
        listing.write_text(json.dumps({"projects": ["Dodeun/nothing"]}))
        env = dict(os.environ)
        env.pop("AUDIT_TOKEN", None)
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--projects", str(listing)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("AUDIT_TOKEN", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_settings_file_that_is_not_json_is_exit_2(self):
        """A JSONDecodeError left to escape is a traceback, and a traceback
        exits 1 - which is the status that means "this Project drifted"."""
        directory = Path(tempfile.mkdtemp(prefix="doden-settings-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "s.json"
        path.write_text("{ not json")
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--settings", str(path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_settings_missing_a_required_field_is_exit_2(self):
        settings = merge({})
        del settings["default_branch"]
        message, status = self.audit(settings)
        self.assertEqual(2, status)
        self.assertIn("default_branch", message)
        self.assertNotIn("Traceback", message)

    def test_a_project_list_of_the_wrong_shape_is_exit_2(self):
        projects = Path(tempfile.mkdtemp(prefix="doden-projects-"))
        self.addCleanup(shutil.rmtree, projects, ignore_errors=True)
        listing = projects / "projects.json"
        listing.write_text(json.dumps({"repositories": ["Dodeun/nothing"]}))
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--projects", str(listing)],
            capture_output=True,
            text=True,
            env=dict(os.environ, AUDIT_TOKEN="not-a-real-token"),
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_settings_carrying_an_unknown_field_is_exit_2(self):
        """A recorded payload the audit does not understand is not a verdict.

        The same reasoning as the schema validator refusing a keyword it does
        not implement: a field silently ignored is a rule that does not exist.
        """
        settings = merge({})
        settings["signed_commits_required"] = True
        message, status = self.audit(settings)
        self.assertEqual(2, status)
        self.assertIn("signed_commits_required", message)


class ReadingSettingsOffGitHub(unittest.TestCase):
    """The one networked file, tested where its answers are ambiguous.

    Two failures look alike from inside `fetch` and mean opposite things: a
    token that may not read a setting, and a request that did not work. The
    first is a question left open; the second is the audit being unable to
    run, and must not be reported in the Project's favour.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from audit import AuditError, NotPermitted, settings

        self.settings = settings
        self.AuditError = AuditError
        self.NotPermitted = NotPermitted

    def responses(self, **by_path):
        """Stand in for the API: a path prefix decides what comes back.

        Longest prefix wins, because every path here begins with the same
        `/repos/owner/name` and a first-match rule would answer the ruleset
        request with the repository.
        """
        answers = sorted(by_path.items(), key=lambda kv: -len(kv[0]))

        def _get(path, token, raw=False):
            for prefix, answer in answers:
                if path.startswith(prefix):
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
            return None

        return _get

    # A repository with no rulesets, no Manifest and no contract document:
    # every rule will have something to say, and none of these tests reads
    # the findings. What they are about is the difference between an answer
    # and a failure to get one.
    REPOSITORY = {
        "/repos/Dodeun/example-project": {"default_branch": "main"},
        "/repos/Dodeun/example-project/rulesets": [],
        "/repos/Dodeun/example-project/rules/branches/main": [],
        "/repos/Dodeun/example-project/contents": None,
    }

    def fetch_with(self, get):
        original = self.settings._get
        self.settings._get = get
        self.addCleanup(setattr, self.settings, "_get", original)
        return self.settings.fetch("Dodeun/example-project", "token")

    def test_a_token_that_may_not_read_variables_leaves_the_question_open(self):
        got = self.fetch_with(
            self.responses(
                **dict(
                    self.REPOSITORY,
                    **{
                        "/repos/Dodeun/example-project/actions/variables":
                            self.NotPermitted("403")
                    },
                )
            )
        )
        self.assertIsNone(got.variables)

    def test_a_404_on_variables_is_not_an_empty_list(self):
        """`[]` says "this Project declares no variables", and that is a pass.

        Nothing that failed is allowed to say it.
        """
        got = self.fetch_with(
            self.responses(
                **dict(
                    self.REPOSITORY,
                    **{"/repos/Dodeun/example-project/actions/variables": None},
                )
            )
        )
        self.assertIsNone(got.variables)

    def test_a_rate_limit_on_variables_is_not_an_unanswered_question(self):
        with self.assertRaises(self.AuditError):
            self.fetch_with(
                self.responses(
                    **dict(
                        self.REPOSITORY,
                        **{
                            "/repos/Dodeun/example-project/actions/variables":
                                self.AuditError("429 rate limited")
                        },
                    )
                )
            )

    def test_the_applied_rules_are_read_for_the_default_branch(self):
        applied = [{"type": "deletion"}]
        got = self.fetch_with(
            self.responses(
                **dict(
                    self.REPOSITORY,
                    **{"/repos/Dodeun/example-project/rules/branches/main": applied},
                )
            )
        )
        self.assertEqual(applied, got.applied_branch_rules)

    def test_a_403_on_the_applied_rules_is_the_audit_failing(self):
        """Not a question left open, as it is for the variables: this needs
        only Metadata: read, so a refusal may be how a lapsed plan answers,
        and *not checked* would leave the run green on that very day."""
        with self.assertRaises(self.AuditError) as caught:
            self.fetch_with(
                self.responses(
                    **dict(
                        self.REPOSITORY,
                        **{
                            "/repos/Dodeun/example-project/rules/branches/main":
                                self.NotPermitted("403")
                        },
                    )
                )
            )
        self.assertNotIsInstance(caught.exception, self.NotPermitted)
        self.assertIn("plan", str(caught.exception))

    def test_a_404_on_the_applied_rules_is_the_audit_failing(self):
        """`[]` is the lapsed-Pro verdict, and a request that found nothing
        must not be able to say it - nor to say *not checked*."""
        with self.assertRaises(self.AuditError):
            self.fetch_with(
                self.responses(
                    **dict(
                        self.REPOSITORY,
                        **{"/repos/Dodeun/example-project/rules/branches/main": None},
                    )
                )
            )

    def test_a_rate_limit_on_the_applied_rules_is_the_audit_failing(self):
        with self.assertRaises(self.AuditError):
            self.fetch_with(
                self.responses(
                    **dict(
                        self.REPOSITORY,
                        **{
                            "/repos/Dodeun/example-project/rules/branches/main":
                                self.AuditError("429 rate limited")
                        },
                    )
                )
            )

    def test_a_repository_that_names_no_default_branch_is_an_error(self):
        with self.assertRaises(self.AuditError):
            self.fetch_with(
                self.responses(**dict(self.REPOSITORY,
                                      **{"/repos/Dodeun/example-project": {}}))
            )


class TheDiscordMessage(AuditTestCase):
    """It says which Project and which rule, never "audit failed"."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from audit import report

        self.report = report

    def verdict(self, *overlays):
        result, _ = self.audit(*(merge(o) for o in overlays))
        return result

    def test_a_clean_run_names_every_project(self):
        message = self.report.discord_message(self.verdict({}))
        self.assertIn("Dodeun/example-project", message)
        self.assertIn("✅", message)

    def test_drift_names_the_project_and_the_rule(self):
        overlay = json.loads(
            (FIXTURES / "fail" / "protect-releases-no-update.json").read_text()
        )
        message = self.report.discord_message(self.verdict(overlay))
        self.assertIn("Dodeun/example-project", message)
        self.assertIn("protect-releases", message)
        self.assertIn("update", message)

    def test_rules_that_stopped_applying_are_named_like_any_other(self):
        overlay = json.loads(
            (FIXTURES / "fail" / "rules-apply-lapsed.json").read_text(encoding="utf-8")
        )
        message = self.report.discord_message(self.verdict(overlay))
        self.assertIn("Dodeun/example-project", message)
        self.assertIn("**rules-apply**", message)
        self.assertIn("lapsed GitHub Pro", message)

    def test_message_and_discord_cannot_be_asked_for_together(self):
        """`--message` promises to post nothing, and a promise a flag
        combination can break is not a promise."""
        directory = Path(tempfile.mkdtemp(prefix="doden-settings-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "s.json"
        path.write_text(json.dumps(merge({})))
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--message", "--discord",
             "--settings", str(path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("not allowed with", proc.stderr)

    def test_a_console_that_cannot_encode_it_is_not_a_failed_audit(self):
        """Measured on Windows, where the console is cp1252 by default.

        Printing ✅ raised UnicodeEncodeError - and in `--discord` mode it did
        so *after* the message had been posted, so a run that had done its
        whole job exited non-zero. PYTHONIOENCODING reproduces it anywhere.
        """
        directory = Path(tempfile.mkdtemp(prefix="doden-settings-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "s.json"
        path.write_text(json.dumps(merge({})))
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--message",
             "--settings", str(path)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("Dodeun/example-project", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_long_message_is_cut_by_whole_lines_and_says_how_many(self):
        overlay = json.loads(
            (FIXTURES / "fail" / "protect-main-missing.json").read_text()
        )
        result = self.verdict(overlay)
        # Thirty Projects' worth of drift is far past Discord's limit, and
        # the count of what was dropped is the one thing that must survive.
        result["projects"] = result["projects"] * 30
        message = self.report.discord_message(result)
        self.assertLessEqual(len(message), self.report.DISCORD_LIMIT)
        self.assertIn("more line(s)", message)
        self.assertNotIn("\n•", message.split("more line(s)")[-1])


class TheContractItJudgesAgainst(unittest.TestCase):
    """The audit reads the contract out of its own checkout.

    "The version a Project pins" and "the version that is current" are then
    the same two files the tier-1 checker ships, rather than two opinions
    held in two places - which is the only reason the `contract-version` and
    `contract-document` rules can be trusted at all.
    """

    def entry_point(self):
        # Loaded by path, not by name: `audit.py` and the `audit/` package
        # share a name, and a plain import gets the package.
        spec = importlib.util.spec_from_file_location(
            "doden_audit_entry", ROOT / "audit.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_real_version_and_document_load_together(self):
        platform = self.entry_point().platform()
        version = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(version.split(".", 1)[0], platform.contract_version)
        self.assertIn(
            f"Contract version: `{platform.contract_version}`",
            platform.contract_document,
        )


APP_FIXTURES = FIXTURES / "app"


def merge_app(overlay: dict) -> dict:
    """Apply an App fixture overlay to the App's conforming settings.

    The same shape as `merge`: `settings` replaces top-level fields and
    merges one level into a dict, where `null` removes a key - which is how
    a fixture takes a permission away.
    """
    app = json.loads((APP_FIXTURES / "conforming.json").read_text(encoding="utf-8"))
    for field, value in (overlay.get("settings") or {}).items():
        if isinstance(value, dict) and isinstance(app.get(field), dict):
            for key, level in value.items():
                if level is None:
                    app[field].pop(key, None)
                else:
                    app[field][key] = level
        else:
            app[field] = value
    return app


class AppTestCase(AuditTestCase):
    def audit_app(self, app: dict, *settings: dict):
        """Run audit.py over a recorded App and recorded Projects.

        The Projects the App is judged against are the ones on the run, as
        they are on a real run: the list is the list, whichever way it was
        read.
        """
        directory = Path(tempfile.mkdtemp(prefix="doden-app-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        app_path = directory / "app.json"
        app_path.write_text(json.dumps(app), encoding="utf-8")
        paths = []
        for index, payload in enumerate(settings or (merge({}),)):
            path = directory / f"{index}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(str(path))
        proc = subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--json",
             "--settings", *paths, "--app-settings", str(app_path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 2:
            return proc.stderr, 2
        return json.loads(proc.stdout), proc.returncode


class TheApp(AppTestCase):
    """Decision 32's second condition: the App cannot widen its own reach.

    Promotion is one-way only while the Prototypes' App can neither add a
    repository to its installation nor already hold a Project. Both are
    settings of the App, not of any repository, so they are judged on a
    record of the App.
    """

    def test_the_app_as_it_was_installed_passes(self):
        result, status = self.audit_app(merge_app({}))
        self.assertEqual(0, status, result)
        app = result["apps"][0]
        self.assertEqual("doden-prototypes", app["app"])
        self.assertEqual([], app["findings"])
        for entry in app["rules"]:
            self.assertEqual("pass", entry["status"], entry["rule"])

    def test_each_failing_fixture_fires_exactly_what_it_claims(self):
        for path in sorted((APP_FIXTURES / "fail").glob("*.json")):
            with self.subTest(fixture=path.name):
                overlay = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(overlay.get("why"))
                result, status = self.audit_app(merge_app(overlay))
                self.assertEqual(1, status, result)
                self.assertTrue(result["projects"][0]["ok"], "the Project did not drift")
                fired = {f["rule"] for f in result["apps"][0]["findings"]}
                self.assertEqual(set(overlay["fires"]), fired)

    def test_each_passing_fixture_comes_back_clean(self):
        for path in sorted((APP_FIXTURES / "pass").glob("*.json")):
            with self.subTest(fixture=path.name):
                overlay = json.loads(path.read_text(encoding="utf-8"))
                result, status = self.audit_app(merge_app(overlay))
                self.assertEqual(0, status, result)
                self.assertEqual([], result["apps"][0]["findings"])

    def test_every_rule_has_a_failing_fixture(self):
        result, _ = self.audit_app(merge_app({}))
        rules = {entry["rule"] for entry in result["apps"][0]["rules"]}
        self.assertEqual({"app-cannot-widen", "app-sees-no-project"}, rules)
        covered = set()
        for path in (APP_FIXTURES / "fail").glob("*.json"):
            covered |= set(json.loads(path.read_text(encoding="utf-8"))["fires"])
        self.assertEqual(rules, covered)

    def test_a_finding_names_the_permission_or_the_repository(self):
        expected = {
            "app-granted-installation-repositories.json": "installation_repositories",
            "app-requests-installation-repositories.json": "installation_repositories",
            "app-sees-a-project.json": "Dodeun/example-project",
            "app-installed-on-everything.json": "all",
        }
        for name, needle in expected.items():
            with self.subTest(fixture=name):
                overlay = json.loads((APP_FIXTURES / "fail" / name).read_text(encoding="utf-8"))
                result, _ = self.audit_app(merge_app(overlay))
                for finding in result["apps"][0]["findings"]:
                    self.assertIn(needle, finding["message"])
                    self.assertIn("doden-prototypes", finding["message"])
                    self.assertTrue(finding["message"].rstrip().endswith("."))

    def test_a_granted_and_a_requested_permission_are_told_apart(self):
        """Granted is held today; requested is one click from being held.
        The operator does something different about each."""
        granted = json.loads(
            (APP_FIXTURES / "fail" / "app-granted-installation-repositories.json").read_text()
        )
        requested = json.loads(
            (APP_FIXTURES / "fail" / "app-requests-installation-repositories.json").read_text()
        )
        held, _ = self.audit_app(merge_app(granted))
        asked, _ = self.audit_app(merge_app(requested))
        self.assertNotEqual(
            held["apps"][0]["findings"][0]["message"],
            asked["apps"][0]["findings"][0]["message"],
        )

    def test_a_project_is_found_whatever_the_case_of_its_name(self):
        """GitHub's names are case-insensitive, and so is a match on them."""
        result, status = self.audit_app(
            merge_app({"settings": {"repositories": ["dodeun/EXAMPLE-project"]}})
        )
        self.assertEqual(1, status, result)
        self.assertEqual(
            {"app-sees-no-project"}, {f["rule"] for f in result["apps"][0]["findings"]}
        )

    def test_a_run_without_an_app_audits_none(self):
        result, status = self.audit(merge({}))
        self.assertEqual(0, status, result)
        self.assertEqual([], result["apps"])

    def test_app_settings_carrying_an_unknown_field_is_exit_2(self):
        app = merge_app({})
        app["webhook_active"] = False
        message, status = self.audit_app(app)
        self.assertEqual(2, status)
        self.assertIn("webhook_active", message)
        self.assertNotIn("Traceback", message)

    def test_app_settings_missing_a_field_is_exit_2(self):
        app = merge_app({})
        del app["granted_permissions"]
        message, status = self.audit_app(app)
        self.assertEqual(2, status)
        self.assertIn("granted_permissions", message)


class TheAppInTheReport(AppTestCase):
    """The report names the App the way it names a Project."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from audit import report

        self.report = report

    def test_a_clean_run_names_the_app(self):
        result, _ = self.audit_app(merge_app({}))
        message = self.report.discord_message(result)
        self.assertIn("✅ App `doden-prototypes`", message)
        self.assertIn("✅ `Dodeun/example-project`", message)
        self.assertIn("doden-prototypes", self.report.text(result))

    def test_drift_names_the_app_and_the_rule(self):
        overlay = json.loads(
            (APP_FIXTURES / "fail" / "app-sees-a-project.json").read_text(encoding="utf-8")
        )
        result, _ = self.audit_app(merge_app(overlay))
        message = self.report.discord_message(result)
        self.assertIn("❌ App `doden-prototypes`", message)
        self.assertIn("**app-sees-no-project**", message)
        self.assertIn("app-sees-no-project", self.report.text(result))

    def test_an_app_that_could_not_be_read_is_named(self):
        result = {
            "contractVersion": "v3",
            "checkedAt": "2026-10-05",
            "ok": False,
            "projects": [],
            "apps": [],
            "unreadable": [{"app": "doden-prototypes", "error": "GET /app answered 401"}],
        }
        self.assertIn(
            "⚠️ App `doden-prototypes` could not be read",
            self.report.discord_message(result),
        )
        self.assertIn(
            "App doden-prototypes: GET /app answered 401", self.report.text(result)
        )


class TheAppsKey(AuditTestCase):
    """An App on the list with no key is the audit unable to run.

    Not a pass and not a silent skip: the list says the App is watched, and
    a run that did not look must not read as one that looked and was happy.
    """

    APP = {"slug": "doden-prototypes", "appId": 5188376, "installationId": 167903513}

    def run_list(self, listing: dict, **env):
        directory = Path(tempfile.mkdtemp(prefix="doden-projects-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "projects.json"
        path.write_text(json.dumps(listing))
        environment = dict(os.environ, AUDIT_TOKEN="not-a-real-token")
        environment.pop("APP_PRIVATE_KEY", None)
        environment.update(env)
        return subprocess.run(
            [sys.executable, str(self.home / "audit.py"), "--projects", str(path)],
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_an_app_on_the_list_without_its_key_is_exit_2(self):
        proc = self.run_list({"projects": [], "app": self.APP})
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("APP_PRIVATE_KEY", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_an_app_entry_of_the_wrong_shape_is_exit_2(self):
        proc = self.run_list(
            {"projects": [], "app": {"slug": "doden-prototypes"}},
            APP_PRIVATE_KEY="not-a-key",
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("appId", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class ReadingTheAppOffGitHub(unittest.TestCase):
    """The App is read with its own key, and the key is used for as little
    as it can be.

    No read-only credential can see a private App: measured 2026-10-04 by
    ticket 35's probe, `GET /apps/{slug}` answered 404 to no token, to the
    audit's fine-grained token and to the operator's own, and both
    `/user/installations` reads answered 403. So the audit holds a key of the
    App's - and spends it on the JWT reads and on minting one token that can
    read metadata and nothing else, revoked when the listing is done.
    """

    APP = {"slug": "doden-prototypes", "appId": 5188376, "installationId": 167903513}
    PERMISSIONS = {"administration": "write", "contents": "write",
                   "metadata": "read", "secrets": "write"}
    FIRST_PAGE = ("GET", "/installation/repositories?per_page=100&page=1")
    SECOND_PAGE = ("GET", "/installation/repositories?per_page=100&page=2")

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from audit import AuditError, settings

        self.settings = settings
        self.AuditError = AuditError
        self.calls = []

    def github(self, overrides=None):
        """Stand in for the API. Records every call; answers by (method, path)."""
        answers = {
            ("GET", "/app"): {"slug": "doden-prototypes", "permissions": dict(self.PERMISSIONS)},
            ("GET", "/app/installations/167903513"): {
                "repository_selection": "selected", "permissions": dict(self.PERMISSIONS),
            },
            ("POST", "/app/installations/167903513/access_tokens"): {"token": "minted"},
            self.FIRST_PAGE: {
                "total_count": 1,
                "repositories": [{"full_name": "Dodeun/doden-prototypes-placeholder"}],
            },
            ("DELETE", "/installation/token"): None,
        }
        answers.update(overrides or {})

        def _request(method, path, token, body=None, raw=False):
            self.calls.append((method, path, token, body))
            answer = answers.get((method, path))
            if isinstance(answer, Exception):
                raise answer
            return answer

        original_request, original_jwt = self.settings._request, self.settings._app_jwt
        self.settings._request = _request
        self.settings._app_jwt = lambda app_id, key: "the-jwt"
        self.addCleanup(setattr, self.settings, "_request", original_request)
        self.addCleanup(setattr, self.settings, "_app_jwt", original_jwt)

    def test_what_is_read_becomes_the_record_the_rules_judge(self):
        self.github()
        got = self.settings.fetch_app(self.APP, "a key")
        self.assertEqual("doden-prototypes", got.app)
        self.assertEqual(self.PERMISSIONS, got.registered_permissions)
        self.assertEqual(self.PERMISSIONS, got.granted_permissions)
        self.assertEqual("selected", got.repository_selection)
        self.assertEqual(["Dodeun/doden-prototypes-placeholder"], got.repositories)

    def test_the_listing_token_can_read_metadata_and_nothing_else(self):
        self.github()
        self.settings.fetch_app(self.APP, "a key")
        mint = [c for c in self.calls if c[0] == "POST"]
        self.assertEqual([{"permissions": {"metadata": "read"}}], [c[3] for c in mint])
        listing = [c for c in self.calls if c[1].startswith("/installation/repositories")]
        self.assertEqual({"minted"}, {c[2] for c in listing})

    def test_the_token_is_revoked_when_the_listing_is_done(self):
        self.github()
        self.settings.fetch_app(self.APP, "a key")
        self.assertEqual(("DELETE", "/installation/token", "minted", None), self.calls[-1])

    def test_the_token_is_revoked_when_the_listing_fails(self):
        self.github({
            self.FIRST_PAGE: self.AuditError("GET /installation/repositories answered 502"),
        })
        with self.assertRaises(self.AuditError):
            self.settings.fetch_app(self.APP, "a key")
        self.assertEqual(("DELETE", "/installation/token", "minted", None), self.calls[-1])

    def test_every_page_of_the_installation_is_read(self):
        names = [{"full_name": f"Dodeun/p{i}"} for i in range(150)]
        self.github({
            self.FIRST_PAGE: {"total_count": 150, "repositories": names[:100]},
            self.SECOND_PAGE: {"total_count": 150, "repositories": names[100:]},
        })
        got = self.settings.fetch_app(self.APP, "a key")
        self.assertEqual(150, len(got.repositories))

    def test_a_key_of_another_app_is_the_audit_failing(self):
        self.github({("GET", "/app"): {"slug": "someone-else", "permissions": {}}})
        with self.assertRaises(self.AuditError) as caught:
            self.settings.fetch_app(self.APP, "a key")
        self.assertIn("someone-else", str(caught.exception))

    def test_an_installation_that_is_gone_is_the_audit_failing(self):
        """`None` here would be an App installed nowhere, which passes every
        rule. An installation id that no longer answers is a question
        nobody answered."""
        self.github({("GET", "/app/installations/167903513"): None})
        with self.assertRaises(self.AuditError) as caught:
            self.settings.fetch_app(self.APP, "a key")
        self.assertIn("167903513", str(caught.exception))

    def test_a_listing_that_comes_up_short_is_the_audit_failing(self):
        """A page that ends early would hide whatever was on the next one."""
        self.github({
            self.FIRST_PAGE: {"total_count": 3, "repositories": [{"full_name": "Dodeun/one"}]},
            self.SECOND_PAGE: {"total_count": 3, "repositories": []},
        })
        with self.assertRaises(self.AuditError):
            self.settings.fetch_app(self.APP, "a key")


@unittest.skipUnless(shutil.which("openssl"), "openssl signs the App's JWT")
class SigningTheAppsJwt(unittest.TestCase):
    """The one thing done with the key itself, checked against a throwaway."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from audit import AuditError, settings

        self.settings = settings
        self.AuditError = AuditError

    def test_the_jwt_verifies_against_the_key_and_names_the_app(self):
        import base64

        directory = Path(tempfile.mkdtemp(prefix="doden-jwt-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        key, public = directory / "key.pem", directory / "key.pub"
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public)],
                       check=True, capture_output=True)

        token = self.settings._app_jwt(5188376, key.read_text())
        head, body, signature = token.split(".")

        def decode(part):
            return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

        claims = json.loads(decode(body))
        self.assertEqual("5188376", claims["iss"])
        self.assertLessEqual(claims["exp"] - claims["iat"], 600, "GitHub refuses more")

        (directory / "signed").write_bytes(f"{head}.{body}".encode())
        (directory / "signature").write_bytes(decode(signature))
        verified = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(public),
             "-signature", str(directory / "signature"), str(directory / "signed")],
            capture_output=True, text=True,
        )
        self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)

    def test_a_key_that_is_not_one_is_the_audit_failing(self):
        with self.assertRaises(self.AuditError):
            self.settings._app_jwt(5188376, "not a key")
