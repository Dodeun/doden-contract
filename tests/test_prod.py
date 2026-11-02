"""Every row of docs/prod.md, shown: a Project production refuses fails check.py.

docs/prod.md lists every refusal of production's list, `doden-infra`'s
`prod-deploy/compose-policy.jq`, beside the rule of check.py that catches it.
Each row has a case here: a conforming Project, the base fixture, changed in
the one way the row describes, which check.py must refuse with exactly the
rules the row names. A row with no case, or a case with no row, fails
`test_every_row_has_a_case`, so the table and the tests cannot drift apart.

The cases are tests/test_lab.py's, written over a Project rather than a
Prototype, and its Stack with the Project's names, `<slug>-api`.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fixtures import CHECK, PROTOTYPE, ROOT, check, merge  # noqa: E402
from test_lab import (  # noqa: E402
    SLUG, Case, backend, env_file, extends, include, joins, label,
    no_database, volume, without_database_addon, worker, write,
)
from test_lab import stack as prototype_stack  # noqa: E402

TABLE = ROOT / "docs" / "prod.md"
DIGEST = "sha256:" + "0" * 64


def stack():
    """The base fixture's Stack, as a dictionary to change: test_lab's, with a Project's names."""
    return json.loads(json.dumps(prototype_stack()).replace(SLUG + "_api", SLUG + "-api"))


class ProjectCase(Case):
    """A row's Project: the base fixture, its Stack changed as the row says."""

    overlay = None

    @staticmethod
    def stack():
        return stack()


def secret(name):
    """The backend takes `name` from Doppler: an environment key with no value."""
    return lambda c: backend(c)["environment"].update({name: None})


CASES = {
    "F1": ProjectCase("prod-file", "x-common", lambda c: c.update({"x-common": {"restart": "always"}})),
    "F2": ProjectCase("prod-file", "name: ${PROJECT_SLUG:?}", lambda c: c.update(name="a-literal-name")),
    "F3": ProjectCase(
        "prod-file networks", "declares no service",
        (lambda c: c.update(services={}), lambda c: c["networks"].pop("data")),
        manifest=without_database_addon,
    ),
    "F4": ProjectCase(
        "prod-file", "by include or by extends",
        files={"docker-compose.prod.yml": include, "extra.yml": "volumes:\n  extra: {}\n"},
    ),
    "F5": ProjectCase(
        "prod-file", "the production host keeps for itself",
        manifest=lambda m: m.update(slug="maintenance", appHost="maintenance.example.test"),
    ),
    "F5b": ProjectCase(
        "prod-file", "pg-",
        manifest=lambda m: m.update(slug="pg-stats", appHost="pg-stats.example.test"),
    ),
    "F6": ProjectCase(
        "prod-file", "by include or by extends",
        files={"docker-compose.prod.yml": extends, "common.yml": "services:\n  base:\n    restart: always\n"},
    ),
    "N1": ProjectCase("networks", "another Stack's", joins("other", {"external": True})),
    "N2": ProjectCase("prod-networks", "leave web and data unnamed", lambda c: c["networks"]["data"].update(name="lab-data")),
    "N3": ProjectCase("networks", "Leave it unnamed", joins("app", {"name": "app"})),
    "N4": ProjectCase("networks", '"database" Add-on', no_database, manifest=without_database_addon),
    "N5": ProjectCase("prod-networks", "sets driver", joins("app", {"driver": "bridge"})),
    "N6": ProjectCase("prod-networks", "ipam", joins("app", {"ipam": {"config": [{"subnet": "10.99.0.0/24"}]}})),
    "V1": ProjectCase("compose-file", "could not render", volume({"size": "1g"})),
    "V2": ProjectCase("prod-volumes", "is external", volume({"external": True})),
    "V3": ProjectCase("prod-volumes", "driver_opts", volume({"driver_opts": {"type": "none", "o": "bind", "device": "/etc"}})),
    "V4": ProjectCase("prod-volumes", "only the local one", volume({"driver": "nfs"})),
    "V5": ProjectCase("prod-volumes", "Leave it unnamed", volume({"name": "shared"})),
    "S1": ProjectCase(
        "prod-services", "production host's own",
        lambda c: c["services"].update(postgres=c["services"].pop("frontend")),
    ),
    "S1b": ProjectCase(
        "prod-services", "starting with a letter or a digit",
        lambda c: c["services"].update({"-worker": c["services"].pop("frontend")}),
    ),
    "S2": ProjectCase("prod-services", "privileged", lambda c: backend(c).update(privileged=True)),
    "S3": ProjectCase("no-published-ports", "publishes", lambda c: backend(c).update(ports=["8080:4000"])),
    "S4": ProjectCase("no-build", "build:", lambda c: backend(c).update(build="./backend")),
    "S5": ProjectCase("no-env-file", "env_file", files={"docker-compose.prod.yml": env_file, "runtime.env": "A=1\n"}),
    "S6": ProjectCase("compose-file", "neither an image", lambda c: backend(c).pop("image")),
    "S6b": ProjectCase(
        "prod-services", "runs its own images",
        lambda c: backend(c).update(image="docker.io/library/nginx:${IMAGE_TAG:?}"),
    ),
    "S6c": ProjectCase(
        "prod-services", "runs its own images",
        lambda c: backend(c).update(image="${IMAGE_REPO_PREFIX}-backend@" + DIGEST),
    ),
    "S6d": ProjectCase("image-tag", "literal tag", lambda c: backend(c).update(image="${IMAGE_REPO_PREFIX}-backend:v1")),
    "S7": ProjectCase("prod-services", "root", lambda c: backend(c).update(user="1000:0")),
    "S7b": ProjectCase("prod-services", "root", lambda c: backend(c).update(user="-0")),
    "S8": ProjectCase("prod-services", "joins no network", worker(network_mode="none", networks=None)),
    "S9": ProjectCase("compose-file", "could not render", lambda c: backend(c)["networks"].append("undeclared")),
    "S10": ProjectCase(
        "prod-services", "aliases",
        lambda c: backend(c).update(networks={"web": None, "data": {"aliases": ["api"]}}),
    ),
    "S11": ProjectCase(
        "compose-file", "could not render",
        lambda c: backend(c).update(volumes=[{"type": "volume", "source": "nope", "target": "/v"}]),
    ),
    "S12": ProjectCase("prod-services", "anonymous volume", lambda c: backend(c).update(volumes=[{"type": "volume", "target": "/v"}])),
    "S13": ProjectCase(
        "prod-services", "mounts a bind",
        lambda c: backend(c).update(volumes=[{"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"}]),
    ),
    "S14": ProjectCase(
        "prod-services", "subpath",
        (volume({}), lambda c: backend(c)["volumes"][0].update(volume={"subpath": "etc"})),
    ),
    "S15": ProjectCase("prod-services", "not the name of an environment variable", secret("A-B")),
    "S16": ProjectCase("prod-services", "DOCKER_HOST", secret("DOCKER_HOST")),
    "L1": ProjectCase("prod-labels", "belongs to Docker Compose", label("com.docker.compose.project", "another")),
    "L2": ProjectCase("prod-labels", "traefik.enable is yes", worker(labels={"traefik.enable": "yes"})),
    "L3": ProjectCase("traefik-labels", "traefik.docker.network=web", label("traefik.docker.network", "data")),
    "L3b": ProjectCase("prod-labels", "Traefik reaches a Project on web", label("Traefik.Docker.Network", "data")),
    "L4": ProjectCase("prod-labels", "Traefik reaches a Project on web", worker(labels={"traefik.docker.network": "other"})),
    "L5": ProjectCase("prod-labels", "lower case", label("traefik.http.routers.%(s)s.Priority", "5")),
    "L6": ProjectCase("prod-labels", "is not a router's", label("traefik.http.routers.%(s)s", "x")),
    "L7": ProjectCase("prod-labels", "not named after the slug", label("traefik.http.routers.api-%(s)s.rule", "Host(`${APP_HOST}`)")),
    "L8": ProjectCase("traefik-names", "as a literal", label("traefik.http.routers.literal.rule", "Host(`${APP_HOST}`)")),
    "L8b": ProjectCase("prod-labels", "not named after the slug", label("Traefik.http.middlewares.literal.headers.customresponseheaders.X", "y")),
    "L9": ProjectCase("prod-labels", "Host(`${APP_HOST}`)", label("traefik.http.routers.%(s)s.rule", "Host(`another.example.test`)")),
    "L10": ProjectCase("prod-labels", "entrypoints", label("traefik.http.routers.%(s)s.entrypoints", "websecure,admin")),
    "L11": ProjectCase("prod-labels", "which is not its own", label("traefik.http.routers.%(s)s.middlewares", "noindex@file")),
    "L12": ProjectCase("prod-labels", "points at the service", label("traefik.http.routers.%(s)s.service", "another")),
    "L13": ProjectCase("prod-labels", "priority", label("traefik.http.routers.%(s)s.priority", "high")),
    "L14": ProjectCase("prod-labels", "tls false", label("traefik.http.routers.%(s)s.tls", "false")),
    "L15": ProjectCase("prod-labels", "certresolver", label("traefik.http.routers.%(s)s.tls.certresolver", "another")),
    "L16": ProjectCase("prod-labels", "not allowed in production", label("traefik.http.routers.%(s)s.observability.accesslogs", "false")),
    "L17": ProjectCase("prod-labels", "is not a service's", label("traefik.http.services.%(s)s", "x")),
    "L18": ProjectCase("prod-labels", "has the port", label("traefik.http.services.%(s)s.loadbalancer.server.port", "eighty")),
    "L19": ProjectCase("prod-labels", "scheme", label("traefik.http.services.%(s)s.loadbalancer.server.scheme", "ftp")),
    "L20": ProjectCase("prod-labels", "not allowed in production", label("traefik.http.services.%(s)s.loadbalancer.sticky.cookie", "true")),
    "L21": ProjectCase("prod-labels", "is not a middleware's", label("traefik.http.middlewares.%(s)s-m", "x")),
    "L22": ProjectCase("prod-labels", "basicauth", label("traefik.http.middlewares.%(s)s-auth.basicauth.users", "a:b")),
    "L23": ProjectCase("prod-labels", "nothing else of Traefik's", label("traefik.tcp.routers.%(s)s.rule", "HostSNI(`*`)")),
    "L24": ProjectCase("prod-labels", "has no rule", label("traefik.http.routers.%(s)s-bare.entrypoints", "websecure")),
    "L25": ProjectCase("prod-labels", "declares no router", worker(labels={"traefik.enable": "true"})),
}


def table():
    from test_lab import table as read

    return read(TABLE)


class ProductionsRefusals(unittest.TestCase):
    def test_every_row_has_a_case(self):
        self.assertEqual(sorted(table()), sorted(CASES))

    def test_each_case_is_refused_by_the_rules_its_row_names(self):
        rows = table()
        for row, case in sorted(CASES.items()):
            with self.subTest(row=row):
                self.assertEqual(rows.get(row), case.rules, f"docs/prod.md's {row} and its case name other rules")
                tree = case.tree()
                self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
                result = check(tree)
                fired = sorted({v["rule"] for v in result["violations"]})
                self.assertEqual(case.rules, fired, result["violations"])
                messages = " ".join(v["message"] for v in result["violations"])
                self.assertIn(case.says, messages)


class WhatProductionAccepts(unittest.TestCase):
    """One Project per rule that production's list lets through, which the lab's may not."""

    def assertPasses(self, *changes, manifest=None):
        tree = ProjectCase("", "", change=changes or (lambda c: None), manifest=manifest).tree()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        self.assertEqual([], check(tree)["violations"])

    def test_the_stack_the_cases_change_passes(self):
        self.assertPasses()

    def test_prod_file_the_slug_written_as_the_manifest_has_it(self):
        """The host renders with the real slug, so a literal one is the same file there."""
        self.assertPasses(lambda c: c.update(name="fixture-base"))

    def test_prod_networks_a_network_of_the_stacks_own_kept_internal(self):
        self.assertPasses(joins("app", {"internal": True}))

    def test_prod_volumes_a_volume_of_the_stacks_own_mounted_with_nocopy(self):
        self.assertPasses(volume({}), lambda c: backend(c)["volumes"][0].update(volume={"nocopy": True}))

    def test_prod_services_a_user_that_is_not_root_and_a_service_that_narrows_itself(self):
        self.assertPasses(
            lambda c: backend(c).update(user="10001:10001", read_only=True, cap_drop=["ALL"]),
            secret("SESSION_SECRET"),
        )

    def test_prod_services_an_image_named_with_the_manifests_slug(self):
        self.assertPasses(lambda c: backend(c).update(image="ghcr.io/dodeun/fixture-base-backend:${IMAGE_TAG:?}"))

    def test_prod_labels_names_with_a_hyphen_and_the_manifests_own_hostname(self):
        """`<slug>-api` is the base fixture's; the hostname, as the Manifest writes it."""
        self.assertPasses(label("traefik.http.routers.%(s)s.rule", "Host(`fixture-base.example.test`)"))


class SecretsDoNotDependOnWhoRendersThem(unittest.TestCase):
    """A secret is a key written with no value that the platform does not fill.

    The host renders with the values it supplies and nothing else, so the
    verdict on `SHELL:` cannot depend on whether the shell running check.py
    exports SHELL, as a CI runner does and the host does not.
    """

    EXPORTED = {"SHELL": "/bin/bash", "USER": "runner", "LOGNAME": "runner", "DOCKER_HOST": "unix:///x.sock"}

    def verdict(self, *changes):
        tree = ProjectCase("", "", change=changes).tree()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        env = dict(os.environ, **self.EXPORTED)
        proc = subprocess.run([sys.executable, str(CHECK), str(tree), "--json"],
                              capture_output=True, text=True, env=env)
        return json.loads(proc.stdout)

    def test_a_secret_named_like_what_the_caller_exports_is_refused(self):
        for name in sorted(self.EXPORTED) + ["HOME", "PATH"]:
            with self.subTest(name=name):
                result = self.verdict(secret(name))
                self.assertEqual(["prod-services"], sorted({v["rule"] for v in result["violations"]}))
                self.assertIn(name, " ".join(v["message"] for v in result["violations"]))

    def test_a_value_written_in_the_file_is_kept(self):
        """`HOME: /srv` is the Project's own value, not a secret, whatever HOME the caller has."""
        result = self.verdict(lambda c: backend(c)["environment"].update(HOME="/srv"))
        self.assertEqual([], result["violations"])

    def test_what_the_platform_supplies_is_not_a_secret(self):
        """The render fills these on the host as well, so nothing is taken from Doppler."""
        result = self.verdict(*(secret(name) for name in ("RELEASE_TAG", "IMAGE_TAG", "PROJECT_SLUG", "APP_HOST")))
        self.assertEqual([], result["violations"])

    def test_what_compose_fills_itself_is_not_a_secret(self):
        """Compose fills PWD and COMPOSE_PROJECT_NAME, `/` and the slug on the host, so neither is null there.

        The host's forbidden names, `COMPOSE_` among them, are read on a key
        left null alone, so neither is refused either.
        """
        result = self.verdict(secret("PWD"), secret("COMPOSE_PROJECT_NAME"))
        self.assertEqual([], result["violations"])


class AProjectOnly(unittest.TestCase):
    """A Prototype is held to the lab's list, and not to production's."""

    def test_a_prototype_with_what_production_alone_refuses_passes(self):
        tree = merge(PROTOTYPE)
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        c = prototype_stack()
        c["networks"]["data"]["name"] = "lab-data"
        backend(c)["image"] = "docker.io/library/nginx:${IMAGE_TAG:?}"
        label("traefik.http.routers.%(s)s.middlewares", "noindex@file")(c)
        c["services"]["postgres"] = c["services"].pop("frontend")
        write(tree, c)
        self.assertEqual([], check(tree)["violations"])


if __name__ == "__main__":
    unittest.main()
