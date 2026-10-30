"""What the lab refuses, read from the repository (ticket 57 of phase 4.3).

A Prototype is deployed by the control host's `lab-deploy`, which holds the
rendered Compose file to a policy of its own: `lab-deploy/compose-policy.jq`
in `doden-control-host`. The deploy user is in the `docker` group, which is
root, and a Prototype's file is written by an agent that may be manipulated,
so the policy lists what is allowed and refuses the rest. Round 1 of ticket
47's trial had two first deploys refused by it - a network of the Stack's
own, then aliases - that this checker had passed. These rules are that
policy, read the same way from the same render, so a Prototype learns what
the lab refuses on its first `check.py`, not on its first deploy.

**A Prototype only.** A Project is deployed by production, which has no such
policy, and these rules would refuse what production accepts (a router
called `<slug>-api`, a bind mount, aliases on a network of the Stack's own).
So each rule passes a tree whose Manifest declares no `prototype` block, and
CONTRACT.md says why the lab is stricter. Where a refusal is already a rule
of the contract's - published ports, `build:`, an env file, an image, the
networks the platform owns, a literal Traefik name - the rule here leaves it
to that one rather than say it twice.

**One copy of the policy, two readers.** docs/lab.md is the table of every
refusal of compose-policy.jq beside the rule here that catches it, or the
reason none can; tests/test_lab.py fails when a row of the table has no test.
The next refusal added to either side shows in that table where the other
must follow.

The render's identity is the checker's sentinels, not the Prototype's own:
the slug is `compose.SENTINEL["PROJECT_SLUG"]` and the hostname
`compose.SENTINEL["APP_HOST"]`, where the lab reads the real ones. A name
derived from the slug is the same test either way; a literal is refused by
`traefik-names` before it reaches here.
"""

import re

from . import compose
from .rules import Violation, rule

SLUG = compose.SENTINEL["PROJECT_SLUG"]
HOST = compose.SENTINEL["APP_HOST"]

# compose-policy.jq's lists, kept in its order so the two read side by side.
ALLOWED_TOP = ("name", "services", "networks", "volumes")
ALLOWED_SERVICE_KEYS = (
    "image", "restart", "command", "entrypoint", "environment", "networks",
    "healthcheck", "labels", "depends_on", "user", "working_dir", "expose",
    "read_only", "tmpfs", "init", "stop_grace_period", "stop_signal",
    "volumes", "mem_limit", "mem_reservation", "cpus", "tty",
    "stdin_open", "cap_drop", "ulimits", "shm_size",
)
# Refused by the lab as keys, and by a rule of the contract's already.
KEYS_OF_OTHER_RULES = {
    "ports": "no-published-ports", "build": "no-build", "env_file": "no-env-file",
}
RESERVED_SERVICES = ("traefik", "postgres-lab", "access-code", "hello")
MIDDLEWARE_TYPES = (
    "stripprefix", "stripprefixregex", "addprefix", "replacepath",
    "replacepathregex", "redirectregex", "redirectscheme", "headers",
    "compress", "ratelimit", "inflightreq", "retry", "buffering",
)
NETWORK_KEYS = ("name", "external", "ipam")
VOLUME_KEYS = ("name", "labels", "driver", "driver_opts", "external")

_ROUTER = re.compile(r"^traefik\.http\.routers\.([^.]+)\.(.+)$")
_SERVICE = re.compile(r"^traefik\.http\.services\.([^.]+)\.(.+)$")
_MIDDLEWARE = re.compile(r"^traefik\.http\.middlewares\.([^.]+)\.([^.]+)")
_RULE = re.compile(
    r"^Host\(`" + re.escape(HOST) + r"`\)( && (Path|PathPrefix)\(`/[A-Za-z0-9._~/-]*`\))?$"
)
_NAME = re.compile(r"^[a-z0-9_-]+$")
_ROOT = re.compile(r"^(root|[+]?0+)$")
_INCLUDE = re.compile(r"""^\s*["']?include["']?\s*:""")
_EXTENDS = re.compile(r"""^(\s*)["']?extends["']?\s*:(.*)$""")
_FILE_KEY = re.compile(r"""(^|[{,\s])["']?file["']?\s*:""")


def _is_prototype(project):
    return project.manifest is not None and project.prototype is not None


def _own(name):
    """A Traefik name of this Prototype's: the slug, or the slug, `_` and more.

    Not `-`: `foo-bar` is a slug too, and Prototype foo naming a service
    `foo-bar` would join foo-bar's load balancer. No slug holds `_`.
    """
    return bool(_NAME.match(name)) and (
        name == SLUG or (name.startswith(SLUG + "_") and len(name) > len(SLUG) + 1)
    )


def _own_ref(name):
    return _own(name[: -len("@docker")] if name.endswith("@docker") else name)


def _derived(name):
    """Moved with the slug: `traefik-names` judges the others, the literals."""
    return SLUG in name


def _show(text):
    """A rendered value, with the sentinels put back as the variables they are."""
    for variable, value in compose.SENTINEL.items():
        text = text.replace(value, "${" + variable + "}")
    return text


def _network_kind(key, definition):
    """web, data, the Stack's own, or None, as compose-policy.jq's net_kind."""
    if definition.get("external") is not True:
        return "own" if definition.get("name") == SLUG + "_" + key else None
    if definition.get("name") == "web":
        return "web"
    if definition.get("name") in ("data", "lab-data"):
        return "data"
    return None


def _lab(rule_id, message):
    return Violation(rule_id, "the lab refuses this Prototype: " + message)


@rule("lab-file", "a Prototype's Compose file has only what the lab reads", needs_render=True)
def lab_file(project):
    """The top of the file: its keys, its name, at least one service, nothing of another file.

    `include`, and `extends` naming a file, are read from the source, as
    `no-env-file` reads `env_file`: the render folds the other file in and
    erases the key. The lab renders this file alone, from stdin, so it
    finds no other. And the slug: lab-deploy.sh refuses one that is the name
    of one of the host's own containers.
    """
    if not _is_prototype(project):
        return
    for index in _other_files(project.compose_source or ""):
        yield _lab(
            "lab-file",
            "docker-compose.prod.yml line " + str(index) + " brings in "
            "another file, by include or by extends. The lab renders this "
            "file alone, so it finds no other: write the Stack in this one.",
        )
    if project.slug in RESERVED_SERVICES:
        yield _lab(
            "lab-file",
            "the slug " + project.slug + " is the name of one of the control "
            "host's own containers, and the lab deploys no Prototype under "
            "it. A Prototype is named otherwise.",
        )
    rendered = project.rendered or {}
    for key in sorted(rendered):
        if key not in ALLOWED_TOP:
            yield _lab(
                "lab-file",
                "the file has a top-level " + key + ", and the lab reads "
                + ", ".join(ALLOWED_TOP) + " and nothing else. What it does "
                "not name it refuses, so a key Compose learns later is refused "
                "until somebody reads it.",
            )
    if rendered.get("name") != SLUG:
        yield _lab(
            "lab-file",
            "the file's name is " + _show(str(rendered.get("name"))) + ", and the "
            "lab deploys a Prototype as its slug alone: name: ${PROJECT_SLUG:?}.",
        )
    if not rendered.get("services"):
        yield _lab("lab-file", "the file declares no service: a Prototype runs at least one.")


def _other_files(source):
    """The lines that bring in another file: `include`, or `extends` with a `file`.

    `extends` without one names a service of this file, which the lab's
    render finds. A line by line reading, as `no-env-file`'s: a `file` key
    on the `extends` line, or on a line below it indented further.
    """
    lines = source.splitlines()
    for index, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            continue
        if _INCLUDE.match(line):
            yield index
            continue
        found = _EXTENDS.match(line)
        if found is None:
            continue
        indent, rest = len(found.group(1)), found.group(2)
        if _FILE_KEY.search(rest):
            yield index
            continue
        for below in lines[index:]:
            if not below.strip() or below.lstrip().startswith("#"):
                continue
            if len(below) - len(below.lstrip()) <= indent:
                break
            if _FILE_KEY.search(below):
                yield index
                break


@rule("lab-networks", "a Prototype's networks are the ones the lab lets a Stack make", needs_render=True)
def lab_networks(project):
    """What a network may set: its name, external, an empty ipam, and internal for its own.

    An external network under another key, and a network of the Stack's own
    given a name, are the `networks` rule's. `web` or `data`, external and
    named neither web, data nor lab-data, is not, because that rule reads
    the key and the lab the name.
    """
    if not _is_prototype(project):
        return
    for key, definition in sorted(((project.rendered or {}).get("networks") or {}).items()):
        kind = _network_kind(key, definition)
        if kind is None:
            if key in ("web", "data") and definition.get("external") is True:
                yield _lab(
                    "lab-networks",
                    "the network " + key + " is external and named "
                    + _show(str(definition.get("name"))) + ". The lab knows a "
                    "network by its name: leave web and data unnamed, as "
                    "external: true alone.",
                )
            continue  # otherwise `networks` says it: another Stack's, or named
        allowed = NETWORK_KEYS + (("internal",) if kind == "own" else ())
        for field in sorted(definition):
            if field not in allowed:
                yield _lab(
                    "lab-networks",
                    "the network " + key + " sets " + field + ". A network a "
                    "Prototype makes is plain: no driver, no options, no "
                    "address, not attachable; internal, if it is its own.",
                )
        if definition.get("ipam"):
            yield _lab(
                "lab-networks",
                "the network " + key + " sets ipam, which chooses its "
                "addresses: the lab gives a Prototype's networks Docker's own.",
            )


@rule("lab-volumes", "a Prototype's volumes are its own, made by the local driver", needs_render=True)
def lab_volumes(project):
    """A volume of the Stack's, named after it, on the local driver, without options.

    `driver_opts` can bind a directory of the host, and an external volume
    or one named in the file can be another Stack's.
    """
    if not _is_prototype(project):
        return
    for key, definition in sorted(((project.rendered or {}).get("volumes") or {}).items()):
        for field in sorted(definition):
            if field not in VOLUME_KEYS:
                yield _lab(
                    "lab-volumes",
                    "the volume " + key + " sets " + field + ": a Prototype's "
                    "volume is declared empty, {}.",
                )
        if definition.get("external") is True:
            yield _lab(
                "lab-volumes",
                "the volume " + key + " is external, and could be another "
                "Stack's. Leave it unnamed and not external: Compose makes it "
                "for this Stack.",
            )
        if definition.get("driver_opts") is not None:
            yield _lab(
                "lab-volumes",
                "the volume " + key + " sets driver_opts, which can bind a "
                "directory of the host into a Prototype.",
            )
        if (definition.get("driver") or "local") != "local":
            yield _lab(
                "lab-volumes",
                "the volume " + key + " uses the driver "
                + str(definition.get("driver")) + ": only the local one.",
            )
        if definition.get("external") is not True and definition.get("name") != SLUG + "_" + key:
            yield _lab(
                "lab-volumes",
                "the volume " + key + " is named " + _show(str(definition.get("name")))
                + ". Leave it unnamed: Compose names it after the Stack, and a "
                "name written in the file is the same volume for every Stack "
                "that copies it.",
            )


@rule("lab-services", "a Prototype's services ask the host for nothing the lab withholds", needs_render=True)
def lab_services(project):
    """No privilege, no host file, no root, no reserved name, networks joined plainly.

    The host, through the deploy user, is root: anything Compose can ask of
    it and the lab does not name - privileged, a capability, a device, the
    host's network or namespaces, pull_policy - is refused. A service named
    like one of the host's own containers would be a second answer for it,
    by Docker's DNS, on any network both join: postgres-lab on lab-data would
    receive every other Prototype's database password. An alias would do the
    same, which is why a network is joined plainly.
    """
    if not _is_prototype(project):
        return
    for name, definition in project.services:
        if name in RESERVED_SERVICES:
            yield _lab(
                "lab-services",
                "service " + name + " is named like one of the control host's "
                "own containers, and would answer for it by Docker's DNS on "
                "every network both join. Name it otherwise.",
            )
        for key in sorted(definition):
            if key not in ALLOWED_SERVICE_KEYS and key not in KEYS_OF_OTHER_RULES:
                yield _lab(
                    "lab-services",
                    "service " + name + " sets " + key + ", which the lab does "
                    "not allow: a Prototype's service configures its own "
                    "process, or narrows what it may do, and asks the host for "
                    "nothing.",
                )
        user = definition.get("user")
        if user is not None and any(_ROOT.match(part) for part in str(user).split(":")):
            yield _lab(
                "lab-services",
                "service " + name + " asks to run as root (user " + str(user)
                + "). A Prototype runs as the user its image declares, or "
                "another that is not root.",
            )
        joined = definition.get("networks") or {}
        if not joined:
            yield _lab(
                "lab-services",
                "service " + name + " joins no network: a Prototype's service "
                "joins web, data with the database Add-on, or a network of the "
                "Stack's own.",
            )
        for network, settings in sorted(joined.items()):
            if settings:
                yield _lab(
                    "lab-services",
                    "service " + name + " joins " + network + " with "
                    + ", ".join(sorted(settings)) + ". A Prototype joins a "
                    "network plainly, without aliases or addresses: an alias "
                    "is a name that answers for every Stack on that network.",
                )
        declared = (project.rendered or {}).get("volumes") or {}
        for mount in definition.get("volumes") or []:
            kind = mount.get("type") if isinstance(mount, dict) else None
            if kind == "volume" and mount.get("source") not in declared:
                # Not declared and named: Compose refuses to render it, which
                # `compose-file` says. So this is an anonymous one.
                yield _lab(
                    "lab-services",
                    "service " + name + " mounts an anonymous volume at "
                    + str(mount.get("target")) + ". Only a volume the file "
                    "declares is the Stack's: declare it under volumes: and "
                    "mount it by its key.",
                )
            elif kind not in ("volume", "tmpfs"):
                yield _lab(
                    "lab-services",
                    "service " + name + " mounts a " + str(kind) + " ("
                    + _show(str(mount.get("source", "") if isinstance(mount, dict) else mount))
                    + "): nothing of the host is mounted into a Prototype, "
                    "only a volume of its Stack, or a tmpfs.",
                )


@rule("lab-labels", "a Prototype's labels ask Traefik only for its own hostname and names", needs_render=True)
def lab_labels(project):
    """A Prototype answers for its own hostname, through routers, services and middlewares of its own.

    Its own: the slug, or the slug, `_` and a suffix. Its rule: Host of its
    hostname, optionally with one Path or PathPrefix. Its entrypoints, tls
    and certresolver: the lab's. Nothing else of Traefik's, and none of
    Docker Compose's own labels, which say whose container this is.
    """
    if not _is_prototype(project):
        return
    for name, definition in project.services:
        labels = compose.labels(definition)
        for message in _label_refusals(labels):
            yield _lab("lab-labels", "service " + name + ": " + message)
        routers = sorted({m.group(1) for k in labels for m in [_ROUTER.match(k.lower())] if m})
        for router in routers:
            if "traefik.http.routers." + router + ".rule" not in labels:
                yield _lab(
                    "lab-labels",
                    "service " + name + ": router " + _show(router) + " has no "
                    "rule, and Traefik would make one up.",
                )
        if labels.get("traefik.enable") == "true" and not routers:
            yield _lab(
                "lab-labels",
                "service " + name + ": traefik.enable is true and it declares "
                "no router, so Traefik would make one up, answering for a name "
                "of its own.",
            )


def _label_refusals(labels):
    routed = any(key.startswith("traefik.http.") for key in labels)
    for key, value in sorted(labels.items()):
        lower = key.lower()
        if lower.startswith("com.docker."):
            yield ("label " + key + " belongs to Docker Compose, which reads it "
                   "to know whose container this is.")
        elif not lower.startswith("traefik."):
            continue
        elif lower == "traefik.enable":
            if value not in ("true", "false"):
                yield "traefik.enable is " + value + ": true or false."
        elif lower == "traefik.docker.network":
            # On a routed service, the label as written is `traefik-labels`'s.
            if value != "web" and not (routed and key == lower):
                yield "label " + key + " is " + value + ": Traefik reaches a Prototype on web."
        elif lower.startswith(("traefik.http.routers.", "traefik.http.services.")):
            if key != lower:
                yield "label " + _show(key) + ": a router's or a service's labels are written in lower case."
            elif lower.startswith("traefik.http.routers."):
                yield from _router_label(key, value)
            else:
                yield from _service_label(key, value)
        elif lower.startswith("traefik.http.middlewares."):
            yield from _middleware_label(key)
        else:
            yield ("label " + _show(key) + " is not allowed: a Prototype has "
                   "http routers, services and middlewares of its own, and "
                   "nothing else of Traefik's.")


def _not_own(kind, name):
    return (kind + " " + _show(name) + " is not named after the slug "
            "(${PROJECT_SLUG} or ${PROJECT_SLUG}_...), so another Prototype "
            "could have it: `-` joins two slugs, `_` cannot.")


def _router_label(key, value):
    found = _ROUTER.match(key)
    if found is None:
        yield "label " + _show(key) + " is not a router's."
        return
    name, part = found.groups()
    if not _own(name):
        if _derived(name):
            yield _not_own("router", name)
        return  # a literal: `traefik-names`
    if part == "rule":
        if not _RULE.match(value):
            yield ("router " + _show(name) + " has the rule " + _show(value)
                   + ": a router of a Prototype answers for Host(`${APP_HOST}`), "
                   "optionally with one Path or PathPrefix.")
    elif part == "entrypoints":
        if any(entry.strip() not in ("web", "websecure") for entry in value.split(",")):
            yield "router " + _show(name) + " uses the entrypoints " + value + ": web and websecure are the only ones."
    elif part == "middlewares":
        for used in (entry.strip() for entry in value.split(",")):
            if not (_own_ref(used) or used == "noindex@file"):
                yield "router " + _show(name) + " uses the middleware " + _show(used) + ", which is not its own."
    elif part == "service":
        if not _own_ref(value):
            yield "router " + _show(name) + " points at the service " + _show(value) + ", which is not its own."
    elif part == "priority":
        if not re.match(r"^[0-9]{1,6}$", value):
            yield "router " + _show(name) + " has the priority " + value + ": a whole number, of six digits at most."
    elif part == "tls":
        if value != "true":
            yield "router " + _show(name) + " has tls " + value + ": true, or no tls label."
    elif part == "tls.certresolver":
        if value != "letsencrypt":
            yield ("router " + _show(name) + " uses the certresolver " + value
                   + ": the lab has letsencrypt, one certificate per hostname.")
    else:
        yield "label " + _show(key) + " is not allowed on the lab."


def _service_label(key, value):
    found = _SERVICE.match(key)
    if found is None:
        yield "label " + _show(key) + " is not a service's."
        return
    name, part = found.groups()
    if not _own(name):
        if _derived(name):
            yield _not_own("Traefik service", name)
        return
    if part == "loadbalancer.server.port":
        if not re.match(r"^[0-9]{1,5}$", value):
            yield "Traefik service " + _show(name) + " has the port " + value + ": the number the container listens on."
    elif part == "loadbalancer.server.scheme":
        if value not in ("http", "https", "h2c"):
            yield "Traefik service " + _show(name) + " has the scheme " + value + ": http, https or h2c."
    else:
        yield "label " + _show(key) + " is not allowed on the lab."


def _middleware_label(key):
    """Read in lower case, as the policy reads it: its labels' case is free."""
    found = _MIDDLEWARE.match(key.lower())
    if found is None:
        yield "label " + _show(key) + " is not a middleware's."
        return
    name, kind = found.groups()
    if not _own(name):
        # A literal is `traefik-names`'s, which reads the label as written:
        # one it cannot read, in another case, is said here.
        if _derived(name) or not key.startswith("traefik.http.middlewares."):
            yield _not_own("middleware", name)
        return
    if kind not in MIDDLEWARE_TYPES:
        yield "middleware " + _show(name) + " is a " + kind + ", which is not allowed on the lab."
