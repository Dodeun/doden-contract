"""Every tier-1 rule, demonstrated twice.

The seam is the checker's public interface: a working tree goes in, a verdict
comes out. Tests run `check.py` as a subprocess, which is the same thing the
reusable workflow runs and the same thing a human runs locally - so a test
passing here is evidence about the command people actually type, not about an
internal function that happens to be reachable from it.

Fixtures are overlays on `fixtures/base`, a conforming Project. A failing
fixture ships only the files that differ, so the fixture *is* the diff that
makes the rule fire. `.remove` in an overlay lists paths to delete from the
merged tree, one per line, which is how "this file is missing" is expressed.

CONTRACT.md is copied into the merged tree from this repository's canonical
copy, exactly as the platform copies it into a Project - so the fixtures
cannot drift from the document they are supposed to satisfy.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "check.py"
FIXTURES = ROOT / "fixtures"


def merge(overlay: str | None) -> Path:
    """Build a working tree: base, plus the overlay, plus the canonical CONTRACT.md."""
    tree = Path(tempfile.mkdtemp(prefix="doden-fixture-"))
    shutil.copytree(FIXTURES / "base", tree, dirs_exist_ok=True)
    removed = set()
    if overlay:
        src = FIXTURES / overlay
        if not src.is_dir():
            raise AssertionError(f"no such fixture: {overlay}")
        shutil.copytree(src, tree, dirs_exist_ok=True)
        remove = tree / ".remove"
        if remove.exists():
            for line in remove.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                target = tree / line
                removed.add(line)
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            remove.unlink()
    if "CONTRACT.md" not in removed and not (tree / "CONTRACT.md").exists():
        shutil.copy(ROOT / "CONTRACT.md", tree / "CONTRACT.md")

    # An overlay writes its .gitignore as `dot-gitignore`, renamed here. A
    # real one would apply to *this* repository too, and would quietly stop
    # the fixture's own .env being committed - leaving a fixture that tests
    # nothing and a suite that stays green while it does.
    disguised = tree / "dot-gitignore"
    if disguised.exists():
        disguised.rename(tree / ".gitignore")

    # A real repository, because "no committed .env" is a question about what
    # is tracked and not about what is lying in the directory. A developer's
    # own .env is correctly ignored and must not fail anyone's check.
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
    return tree


def check(tree: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CHECK), str(tree), "--json"],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"check.py emitted no JSON verdict (exit {proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )


class FixtureCase(unittest.TestCase):
    """Base class: run a fixture and assert exactly which rules fired."""

    def verdict(self, overlay: str | None):
        tree = merge(overlay)
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        return check(tree)

    def assertPasses(self, overlay: str | None):
        result = self.verdict(overlay)
        self.assertEqual(
            [],
            result["violations"],
            f"{overlay or 'base'} is meant to be a conforming Project",
        )
        self.assertTrue(result["ok"])

    def assertFails(self, overlay: str, rule: str, *, names: str):
        """The rule fires, nothing else does, and the message names what to fix."""
        result = self.verdict(overlay)
        fired = sorted({v["rule"] for v in result["violations"]})
        self.assertEqual(
            [rule], fired, f"{overlay} should violate {rule} and nothing else"
        )
        self.assertFalse(result["ok"])
        messages = " ".join(v["message"] for v in result["violations"])
        self.assertIn(
            names,
            messages,
            f"{overlay}'s message does not name what to fix: {messages!r}",
        )


class ConformingProjects(FixtureCase):
    def test_the_base_fixture_passes(self):
        self.assertPasses(None)

    def test_a_project_without_the_database_addon_passes(self):
        self.assertPasses("pass/no-database")


class Manifest(FixtureCase):
    def test_a_project_with_no_manifest_is_refused(self):
        self.assertFails("fail/manifest-missing", "manifest", names="platform.json")

    def test_a_manifest_that_is_not_json_is_refused(self):
        self.assertFails("fail/manifest-not-json", "manifest", names="valid JSON")

    def test_an_unknown_profile_is_refused(self):
        self.assertFails("fail/manifest-unknown-profile", "manifest", names="profile")

    def test_a_missing_required_field_is_refused(self):
        self.assertFails("fail/manifest-missing-field", "manifest", names="appHost")

    def test_the_database_addon_must_declare_a_seed_command(self):
        self.assertFails("fail/manifest-no-seed-command", "manifest", names="seedCommand")

    def test_an_unknown_field_is_refused(self):
        self.assertFails("fail/manifest-unknown-field", "manifest", names="apphost")

    def test_a_v1_shaped_manifest_is_refused_in_the_versions_own_terms(self):
        """The message a person actually meets when a Project is left behind.

        They have not read the ticket that changed the shape and may not know
        a version changed at all, so it has to name both versions and show
        the new shape - not report that a value failed an assertion.
        """
        result = self.verdict("fail/manifest-addons-v1-shape")
        self.assertEqual(
            ["manifest"], sorted({v["rule"] for v in result["violations"]})
        )
        message = " ".join(v["message"] for v in result["violations"])
        for names in ("v1", "v2", '"provider": "postgresql"', "@v1"):
            self.assertIn(names, message)

    def test_the_oauth_addon_is_gone_and_the_message_says_why(self):
        self.assertFails(
            "fail/manifest-oauth-addon", "manifest", names="ADR-0013",
        )


class Addons(FixtureCase):
    """An Add-on is an optional dependency on a Shared Platform Service.

    Which means three things the checker can ask: the provider is one that
    exists, the Stack is handed the way to reach it, and a Project that
    declares nothing is handed nothing.
    """

    def test_a_provider_the_platform_does_not_run_is_refused(self):
        self.assertFails(
            "fail/addon-provider", "addon-provider", names="postgresql",
        )

    def test_declaring_a_database_without_passing_its_url_is_refused(self):
        self.assertFails(
            "fail/database-url-missing", "database-url", names="DATABASE_URL",
        )

    def test_passing_a_database_url_without_declaring_one_is_refused(self):
        self.assertFails(
            "fail/database-url-undeclared", "database-url", names="backend",
        )


class TheChannelBackToThePlatform(FixtureCase):
    def test_a_project_carrying_no_findings_file_is_refused(self):
        self.assertFails(
            "fail/platform-findings-missing", "platform-findings",
            names="PLATFORM-FINDINGS.md",
        )

    def test_the_message_does_not_say_where_the_findings_go(self):
        """The channel is one-way, and the rule must not leak the reverse.

        A Project never needs to know that the platform's own repository
        exists. A message naming it would put the address in every Project
        that ever forgets the file.
        """
        result = self.verdict("fail/platform-findings-missing")
        message = " ".join(v["message"] for v in result["violations"])
        for address in ("ai-archi-brainstorm", "Dodeun/", "doden-contract"):
            self.assertNotIn(address, message)


class ContractDocument(FixtureCase):
    def test_a_project_carrying_no_contract_document_is_refused(self):
        self.assertFails(
            "fail/contract-version-no-document", "contract-version",
            names="CONTRACT.md",
        )

    def test_a_contract_document_pinning_another_version_is_refused(self):
        self.assertFails(
            "fail/contract-version-stale-document", "contract-version",
            names="v9",
        )

    def test_a_manifest_pinning_another_version_is_refused(self):
        self.assertFails(
            "fail/contract-version-stale-manifest", "contract-version",
            names="contractVersion",
        )


class ProductionComposeFile(FixtureCase):
    def test_a_project_with_no_production_compose_file_is_refused(self):
        self.assertFails(
            "fail/compose-file-missing", "compose-file",
            names="docker-compose.prod.yml",
        )

    def test_a_compose_file_that_does_not_render_is_refused(self):
        self.assertFails(
            "fail/compose-file-unrenderable", "compose-file",
            names="docker compose config",
        )

    def test_a_compose_file_needing_a_variable_the_platform_does_not_supply_is_refused(self):
        self.assertFails(
            "fail/compose-file-extra-required-variable", "compose-file",
            names="SOME_OTHER_SECRET",
        )


class TheStacksShape(FixtureCase):
    def test_a_published_port_is_refused(self):
        self.assertFails(
            "fail/published-ports", "no-published-ports", names="frontend",
        )

    def test_a_port_published_through_a_variable_is_refused(self):
        self.assertFails(
            "fail/published-ports-through-a-variable", "no-published-ports",
            names="8080",
        )

    def test_a_build_key_is_refused(self):
        self.assertFails("fail/build-key", "no-build", names="backend")

    def test_the_latest_tag_is_refused(self):
        self.assertFails("fail/image-tag-latest", "image-tag", names="latest")

    def test_a_floating_tag_that_is_not_latest_is_refused(self):
        # This contract's own release process moves `v1` every release, so a
        # denylist of obvious names would have let this through.
        self.assertFails("fail/image-tag-floating", "image-tag", names=":v1")

    def test_an_image_with_no_tag_is_refused(self):
        self.assertFails("fail/image-tag-absent", "image-tag", names="frontend")

    def test_a_service_with_no_healthcheck_is_refused(self):
        self.assertFails(
            "fail/healthcheck-missing", "healthchecks", names="frontend",
        )

    def test_a_healthcheck_that_is_switched_off_is_refused(self):
        self.assertFails(
            "fail/healthcheck-disabled", "healthchecks", names="disables",
        )


class RoutingAndNetworks(FixtureCase):
    def test_a_routed_service_that_does_not_enable_traefik_is_refused(self):
        self.assertFails(
            "fail/traefik-enable-missing", "traefik-labels", names="traefik.enable",
        )

    def test_a_routed_service_that_does_not_name_its_network_is_refused(self):
        self.assertFails(
            "fail/traefik-network-missing", "traefik-labels",
            names="traefik.docker.network",
        )

    def test_router_names_written_as_literals_are_refused(self):
        self.assertFails(
            "fail/traefik-names-literal", "traefik-names", names="my-project",
        )

    def test_a_web_network_the_stack_creates_itself_is_refused(self):
        self.assertFails(
            "fail/network-web-not-external", "networks", names="external",
        )

    def test_a_service_that_does_not_join_web_is_refused(self):
        self.assertFails(
            "fail/network-web-not-joined", "networks", names="frontend",
        )

    def test_joining_data_without_the_database_addon_is_refused(self):
        self.assertFails(
            "fail/network-data-undeclared", "networks", names="database",
        )

    def test_declaring_the_database_addon_without_joining_data_is_refused(self):
        self.assertFails(
            "fail/network-data-missing", "networks", names="data network",
        )


class ConfigurationAndImages(FixtureCase):
    def test_a_default_on_a_platform_supplied_variable_is_refused(self):
        self.assertFails(
            "fail/identity-variable-default", "identity-variables",
            names="IMAGE_REPO_PREFIX",
        )

    def test_a_platform_supplied_variable_that_does_not_fail_when_unset_is_refused(self):
        self.assertFails(
            "fail/identity-variable-not-required", "identity-variables",
            names="PROJECT_SLUG",
        )

    def test_an_env_file_is_refused(self):
        self.assertFails("fail/env-file", "no-env-file", names="env_file")

    def test_a_committed_env_is_refused(self):
        self.assertFails("fail/committed-env", "no-committed-env", names=".env")

    def test_an_ignored_env_is_not_a_committed_env(self):
        self.assertPasses("pass/ignored-env")

    def test_a_single_stage_dockerfile_is_refused(self):
        self.assertFails(
            "fail/single-stage-dockerfile", "multi-stage-dockerfiles",
            names="backend/Dockerfile",
        )


class ReportedNotEnforced(FixtureCase):
    """A note must never be able to change a verdict.

    This is what carries two separate promises: image size is "reported, not
    enforced", and CI and a laptop reach the same verdict on the same tree
    even though they can see different things.
    """

    def test_a_conforming_project_still_reports_its_images(self):
        result = self.verdict(None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["notes"], "the images a deploy pulls go unreported")
        self.assertTrue(
            any("pulls" in note for note in result["notes"]),
            result["notes"],
        )

    def test_notes_are_not_violations(self):
        result = self.verdict("fail/published-ports")
        rules = {entry["rule"] for entry in result["rules"]}
        for note in result["notes"]:
            self.assertNotIn(note, [v["message"] for v in result["violations"]])
        self.assertNotIn("image-size", rules, "a report must not be a rule")


class TheCheckerFailingIsNotTheProjectFailing(unittest.TestCase):
    """Exit 2 means "could not run", and must never read as exit 1.

    A checker that reports its own breakage as a contract violation sends
    somebody to edit a Compose file to fix a missing dependency.
    """

    def test_a_schema_keyword_the_validator_does_not_implement_exits_2(self):
        tree = merge(None)
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)

        checker = Path(tempfile.mkdtemp(prefix="doden-checker-"))
        self.addCleanup(shutil.rmtree, checker, ignore_errors=True)
        for name in ("check.py", "VERSION", "CONTRACT.md", "platform.schema.json"):
            shutil.copy(ROOT / name, checker / name)
        shutil.copytree(ROOT / "contract", checker / "contract")

        schema = json.loads((checker / "platform.schema.json").read_text())
        schema["properties"]["slug"]["multipleOf"] = 2
        (checker / "platform.schema.json").write_text(json.dumps(schema))

        proc = subprocess.run(
            [sys.executable, str(checker / "check.py"), str(tree)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("multipleOf", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
