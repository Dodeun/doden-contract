"""Every row of docs/lab.md, shown: a Prototype the lab refuses fails check.py.

docs/lab.md lists every refusal of the control host's compose-policy.jq
beside the rule of check.py that catches it. Each row has a case here: a
conforming Prototype, `pass/prototype`, changed in the one way the row
describes, which check.py must refuse with exactly the rules the row names.
A row with no case, or a case with no row, fails `test_every_row_has_a_case`,
so the table and the tests cannot drift apart.

The Stacks are written as JSON, which is YAML, so that a case is a change to
a dictionary rather than to text. Labels are changed as a map and written as
a list, `key=value`: Compose interpolates a whole list entry, and not the
keys of a map. Volumes are mounted in the long form: on Windows, Compose
reads `v:/data` as a drive.
"""

import json
import re
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fixtures import PROTOTYPE, ROOT, check, merge  # noqa: E402

sys.path.insert(0, str(ROOT))

TABLE = ROOT / "docs" / "lab.md"
S = "${PROJECT_SLUG:?}"
IMAGE = "${IMAGE_REPO_PREFIX}-%s:${IMAGE_TAG:?IMAGE_TAG is not set}"
HEALTHCHECK = {"test": ["CMD-SHELL", "true"], "interval": "10s"}


def stack():
    """pass/prototype's Stack, as a dictionary to change."""
    return {
        "name": "${PROJECT_SLUG:?PROJECT_SLUG is not set}",
        "services": {
            "backend": {
                "image": IMAGE % "backend",
                "restart": "unless-stopped",
                "environment": {"NODE_ENV": "production", "DATABASE_URL": None},
                "networks": ["web", "data"],
                "healthcheck": HEALTHCHECK,
                "labels": {
                    "traefik.enable": "true",
                    "traefik.docker.network": "web",
                    "traefik.http.routers.%s_api.rule" % S: "Host(`${APP_HOST}`) && PathPrefix(`/api`)",
                    "traefik.http.routers.%s_api.entrypoints" % S: "websecure",
                    "traefik.http.routers.%s_api.tls.certresolver" % S: "letsencrypt",
                    "traefik.http.routers.%s_api.middlewares" % S: "%s_api_strip" % S,
                    "traefik.http.middlewares.%s_api_strip.stripprefix.prefixes" % S: "/api",
                    "traefik.http.services.%s_api.loadbalancer.server.port" % S: "4000",
                },
            },
            "frontend": {
                "image": IMAGE % "frontend",
                "restart": "unless-stopped",
                "depends_on": ["backend"],
                "networks": ["web"],
                "healthcheck": HEALTHCHECK,
                "labels": {
                    "traefik.enable": "true",
                    "traefik.docker.network": "web",
                    "traefik.http.routers.%s.rule" % S: "Host(`${APP_HOST}`)",
                    "traefik.http.routers.%s.entrypoints" % S: "websecure",
                    "traefik.http.routers.%s.tls.certresolver" % S: "letsencrypt",
                    "traefik.http.services.%s.loadbalancer.server.port" % S: "80",
                },
            },
        },
        "networks": {"web": {"external": True}, "data": {"external": True}},
    }


# -- what a case changes ------------------------------------------------------

def backend(c):
    return c["services"]["backend"]


def front_labels(c):
    return c["services"]["frontend"]["labels"]


def worker(**extra):
    """A service Traefik does not route to, on web, for the rows about one."""
    def add(c):
        definition = dict(image=IMAGE % "worker", healthcheck=HEALTHCHECK, networks=["web"])
        definition.update(extra)
        c["services"]["worker"] = {k: v for k, v in definition.items() if v is not None}
    return add


def joins(network, definition):
    """The backend joins a network the file declares."""
    def add(c):
        c["networks"][network] = definition
        backend(c)["networks"].append(network)
    return add


def volume(definition):
    """The backend mounts the volume `v`, declared as given."""
    def add(c):
        c["volumes"] = {"v": definition}
        backend(c)["volumes"] = [{"type": "volume", "source": "v", "target": "/v"}]
    return add


def label(key, value, service=front_labels):
    def add(c):
        service(c)[key % {"s": S}] = value
    return add


def no_database(c):
    backend(c)["environment"].pop("DATABASE_URL")


def without_database_addon(manifest):
    manifest["addons"].pop("database")


class Case:
    def __init__(self, rules, names, change=None, manifest=None, files=None):
        self.rules = sorted(rules.split())
        self.names = names
        self.change = change
        self.manifest = manifest
        self.files = files or {}

    def tree(self):
        tree = merge(PROTOTYPE)
        if self.change is not None:
            c = stack()
            for change in self.change if isinstance(self.change, tuple) else (self.change,):
                change(c)
            write(tree, c)
        if self.manifest is not None:
            path = tree / "platform.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.manifest(manifest)
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for name, text in self.files.items():
            (tree / name).write_text(text(tree) if callable(text) else text, encoding="utf-8")
        return tree


def write(tree, c):
    for definition in c["services"].values():
        if isinstance(definition.get("labels"), dict):
            definition["labels"] = [k + "=" + v for k, v in definition["labels"].items()]
    (tree / "docker-compose.prod.yml").write_text(json.dumps(c, indent=2), encoding="utf-8")


def include(tree):
    """The fixture's YAML, including a second file: the key the render erases."""
    source = (tree / "docker-compose.prod.yml").read_text(encoding="utf-8")
    return "include:\n  - extra.yml\n" + source


def env_file(tree):
    """The fixture's YAML, with an env_file: the key the render erases."""
    source = (tree / "docker-compose.prod.yml").read_text(encoding="utf-8")
    return source.replace("  backend:\n", "  backend:\n    env_file: runtime.env\n", 1)


CASES = {
    "F1": Case("lab-file", "x-common", lambda c: c.update({"x-common": {"restart": "always"}})),
    "F2": Case("lab-file", "name: ${PROJECT_SLUG:?}", lambda c: c.update(name="a-literal-name")),
    "F3": Case(
        "lab-file networks", "declares no service",
        (lambda c: c.update(services={}), lambda c: c["networks"].pop("data")),
        manifest=without_database_addon,
    ),
    "F4": Case(
        "lab-file", "includes other files",
        files={"docker-compose.prod.yml": include, "extra.yml": "volumes:\n  extra: {}\n"},
    ),
    "N1": Case("networks", "another Stack's", joins("other", {"external": True})),
    "N2": Case("lab-networks", "web is named web", lambda c: c["networks"]["web"].update(name="other-web")),
    "N3": Case("networks", "Leave it unnamed", joins("app", {"name": "app"})),
    "N4": Case("networks", '"database" Add-on', no_database, manifest=without_database_addon),
    "N5": Case("lab-networks", "sets driver", joins("app", {"driver": "bridge"})),
    "N6": Case("lab-networks", "ipam", joins("app", {"ipam": {"config": [{"subnet": "10.99.0.0/24"}]}})),
    "V1": Case("compose-file", "could not render", volume({"size": "1g"})),
    "V2": Case("lab-volumes", "is external", volume({"external": True})),
    "V3": Case("lab-volumes", "driver_opts", volume({"driver_opts": {"type": "none", "o": "bind", "device": "/etc"}})),
    "V4": Case("lab-volumes", "only the local one", volume({"driver": "nfs"})),
    "V5": Case("lab-volumes", "Leave it unnamed", volume({"name": "shared"})),
    "S1": Case("lab-services", "control host's", lambda c: c["services"].update(traefik=c["services"].pop("frontend"))),
    "S2": Case("lab-services", "privileged", lambda c: backend(c).update(privileged=True)),
    "S3": Case("no-published-ports", "publishes", lambda c: backend(c).update(ports=["8080:4000"])),
    "S4": Case("no-build", "build:", lambda c: backend(c).update(build="./backend")),
    "S5": Case("no-env-file", "env_file", files={"docker-compose.prod.yml": env_file, "runtime.env": "A=1\n"}),
    "S6": Case("compose-file", "neither an image", lambda c: backend(c).pop("image")),
    "S7": Case("lab-services", "root", lambda c: backend(c).update(user="1000:0")),
    "S8": Case("lab-services", "joins no network", worker(network_mode="none", networks=None)),
    "S9": Case("compose-file", "could not render", lambda c: backend(c)["networks"].append("undeclared")),
    "S10": Case(
        "lab-services", "aliases",
        lambda c: backend(c).update(networks={"web": None, "data": {"aliases": ["api"]}}),
    ),
    "S11": Case(
        "compose-file", "could not render",
        lambda c: backend(c).update(volumes=[{"type": "volume", "source": "nope", "target": "/v"}]),
    ),
    "S12": Case("lab-services", "anonymous volume", lambda c: backend(c).update(volumes=[{"type": "volume", "target": "/v"}])),
    "S13": Case(
        "lab-services", "mounts a bind",
        lambda c: backend(c).update(volumes=[{"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"}]),
    ),
    "L1": Case("lab-labels", "belongs to Docker Compose", label("com.docker.compose.project", "another")),
    "L2": Case("lab-labels", "traefik.enable is yes", worker(labels={"traefik.enable": "yes"})),
    "L3": Case("traefik-labels", "traefik.docker.network=web", label("traefik.docker.network", "data")),
    "L4": Case("lab-labels", "Traefik reaches a Prototype on web", worker(labels={"traefik.docker.network": "other"})),
    "L5": Case("lab-labels", "lower case", label("traefik.http.routers.%(s)s.Priority", "5")),
    "L6": Case("lab-labels", "is not a router's", label("traefik.http.routers.%(s)s", "x")),
    "L7": Case("lab-labels", "not named after the slug", label("traefik.http.routers.%(s)s-api.rule", "Host(`${APP_HOST}`)")),
    "L8": Case("traefik-names", "as a literal", label("traefik.http.routers.literal.rule", "Host(`${APP_HOST}`)")),
    "L9": Case("lab-labels", "Host(`${APP_HOST}`)", label("traefik.http.routers.%(s)s.rule", "Host(`another.lab.doden.dev`)")),
    "L10": Case("lab-labels", "entrypoints", label("traefik.http.routers.%(s)s.entrypoints", "websecure,admin")),
    "L11": Case("lab-labels", "which is not its own", label("traefik.http.routers.%(s)s.middlewares", "access-code@file")),
    "L12": Case("lab-labels", "points at the service", label("traefik.http.routers.%(s)s.service", "another")),
    "L13": Case("lab-labels", "priority", label("traefik.http.routers.%(s)s.priority", "high")),
    "L14": Case("lab-labels", "tls false", label("traefik.http.routers.%(s)s.tls", "false")),
    "L15": Case("lab-labels", "certresolver", label("traefik.http.routers.%(s)s.tls.certresolver", "another")),
    "L16": Case("lab-labels", "not allowed on the lab", label("traefik.http.routers.%(s)s.observability.accesslogs", "false")),
    "L17": Case("lab-labels", "is not a service's", label("traefik.http.services.%(s)s", "x")),
    "L18": Case("lab-labels", "has the port", label("traefik.http.services.%(s)s.loadbalancer.server.port", "eighty")),
    "L19": Case("lab-labels", "scheme", label("traefik.http.services.%(s)s.loadbalancer.server.scheme", "ftp")),
    "L20": Case("lab-labels", "not allowed on the lab", label("traefik.http.services.%(s)s.loadbalancer.sticky.cookie", "true")),
    "L21": Case("lab-labels", "is not a middleware's", label("traefik.http.middlewares.%(s)s_m", "x")),
    "L22": Case("lab-labels", "basicauth", label("traefik.http.middlewares.%(s)s_auth.basicauth.users", "a:b")),
    "L23": Case("lab-labels", "nothing else of Traefik's", label("traefik.tcp.routers.%(s)s.rule", "HostSNI(`*`)")),
    "L24": Case("lab-labels", "has no rule", label("traefik.http.routers.%(s)s_bare.entrypoints", "websecure")),
    "L25": Case("lab-labels", "declares no router", worker(labels={"traefik.enable": "true"})),
}


def table():
    """The rows of docs/lab.md: id, and the rules its last column names before any colon."""
    from contract.rules import RULES  # the checker's own, so a renamed rule shows

    known = {fn.rule_id for fn in RULES}
    rows = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^\| ([FNVSL][0-9]+) \|.*\| (.+) \|$", line)
        if found:
            named = found.group(2).split(":", 1)[0]
            rows[found.group(1)] = sorted(t for t in re.findall(r"`([a-z0-9-]+)`", named) if t in known)
    return rows


class TheLabsRefusals(unittest.TestCase):
    def test_every_row_has_a_case(self):
        self.assertEqual(sorted(table()), sorted(CASES))

    def test_each_case_is_refused_by_the_rules_its_row_names(self):
        rows = table()
        for row, case in sorted(CASES.items()):
            with self.subTest(row=row):
                self.assertEqual(rows.get(row), case.rules, f"docs/lab.md's {row} and its case name other rules")
                tree = case.tree()
                self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
                result = check(tree)
                fired = sorted({v["rule"] for v in result["violations"]})
                self.assertEqual(case.rules, fired, result["violations"])
                messages = " ".join(v["message"] for v in result["violations"])
                self.assertIn(case.names, messages)

    def test_the_stack_the_cases_change_passes(self):
        tree = Case("", "", change=lambda c: None).tree()
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        self.assertEqual([], check(tree)["violations"])


class APrototypeOnly(unittest.TestCase):
    """Production has no such policy: a Project is not held to the lab's rules."""

    def test_a_project_with_what_the_lab_alone_refuses_passes(self):
        tree = merge(None)
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        c = stack()
        backend(c)["networks"] = {"web": None, "data": None, "app": {"aliases": ["api"]}}
        c["services"]["frontend"]["networks"] = ["web", "app"]
        c["networks"]["app"] = {}
        label("traefik.http.routers.%(s)s-extra.rule", "Host(`${APP_HOST}`)")(c)
        write(tree, c)
        self.assertEqual([], check(tree)["violations"])


if __name__ == "__main__":
    unittest.main()
