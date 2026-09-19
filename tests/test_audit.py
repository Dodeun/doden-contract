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
STUB_VERSION = "v1.0.0"


def install() -> Path:
    """A copy of the audit with a stub contract, so fixtures stay small."""
    home = Path(tempfile.mkdtemp(prefix="doden-audit-"))
    shutil.copy(ROOT / "audit.py", home / "audit.py")
    shutil.copytree(ROOT / "audit", home / "audit")
    (home / "VERSION").write_text(STUB_VERSION)
    (home / "CONTRACT.md").write_text(CANONICAL_DOCUMENT, encoding="utf-8")
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
