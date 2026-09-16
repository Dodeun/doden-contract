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

    def test_an_image_with_no_tag_is_refused(self):
        self.assertFails("fail/image-tag-absent", "image-tag", names="frontend")

    def test_a_service_with_no_healthcheck_is_refused(self):
        self.assertFails(
            "fail/healthcheck-missing", "healthchecks", names="frontend",
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
