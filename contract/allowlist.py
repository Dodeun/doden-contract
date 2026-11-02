"""What a deploy host lets a Compose file ask of it, read from the repository.

Two hosts hold a Stack to a list of what it may ask for, and refuse the
rest: the control host's `lab-deploy`, for a Prototype
(`lab-deploy/compose-policy.jq` in `doden-control-host`), and production's
`prod-deploy`, for a Project (`prod-deploy/compose-policy.jq` in
`doden-infra`). Each deploy user is in the `docker` group, which is root, so
the list is what stands between a Compose file and the host. Production's
list is the lab's with four differences (decision 64 of phase 5), so this
module reads both: `AllowList` is the lab's reading, and each host's module
says what differs - `lab.py` its reserved names and its `_` alone, `prod.py`
its hostname, its images and its secrets.

**The render, as the host renders it.** The rules read the file Compose
renders, as both hosts do, with the checker's sentinels for the slug and the
hostname where the hosts have the real ones. A name derived from the slug
is the same test either way; a literal is refused by `traefik-names`
before it reaches here.

Each method yields the messages of one rule, without the rule's id; the
host's module turns them into violations. Where a refusal is already a rule
of the contract's - published ports, `build:`, an env file, an image's tag,
the networks the platform owns, a literal Traefik name - the list leaves it
to that one rather than say it twice.
"""

import re

from . import compose
from .rules import Violation

SLUG = compose.SENTINEL["PROJECT_SLUG"]
HOST = compose.SENTINEL["APP_HOST"]

# compose-policy.jq's lists, the same on both hosts, kept in its order so the
# two read side by side.
ALLOWED_TOP = ("name", "services", "networks", "volumes")
ALLOWED_SERVICE_KEYS = (
    "image", "restart", "command", "entrypoint", "environment", "networks",
    "healthcheck", "labels", "depends_on", "user", "working_dir", "expose",
    "read_only", "tmpfs", "init", "stop_grace_period", "stop_signal",
    "volumes", "mem_limit", "mem_reservation", "cpus", "tty",
    "stdin_open", "cap_drop", "ulimits", "shm_size",
)
# Refused by both hosts as keys, and by a rule of the contract's already.
KEYS_OF_OTHER_RULES = {
    "ports": "no-published-ports", "build": "no-build", "env_file": "no-env-file",
}
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
_NAME = re.compile(r"^[a-z0-9_-]+$")
# Root by name or by any number of zeros, signed or not, as the user or the
# group: Docker reads `-0` as uid 0 too (gap 10), as the Template's test.yml
# guard reads it.
_ROOT = re.compile(r"^(root|[+-]?0+)$")
_INCLUDE = re.compile(r"""^\s*["']?include["']?\s*:""")
_EXTENDS = re.compile(r"""^(\s*)["']?extends["']?\s*:(.*)$""")
_FILE_KEY = re.compile(r"""(^|[{,\s])["']?file["']?\s*:""")


def show(text):
    """A rendered value, with the sentinels put back as the variables they are."""
    for variable, value in compose.SENTINEL.items():
        text = text.replace(value, "${" + variable + "}")
    return text


def other_files(source):
    """The lines that bring in another file: `include`, or `extends` with a `file`.

    `extends` without one names a service of this file, which the host's
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


class AllowList:
    """One host's list. The defaults are the lab's; production's module overrides them."""

    place = "the lab"          # who refuses, inside a sentence
    on_place = "on the lab"    # where a label is not allowed
    who = "Prototype"          # what the host deploys
    host_own = "the control host's own containers"
    reserved_services = ()
    data_names = ("data", "lab-data")
    separators = ("_",)        # what may follow the slug in a Traefik name
    file_middlewares = ()      # middlewares of Traefik's file provider it allows
    not_own_why = ""

    # -- what each host says differently --------------------------------

    def applies(self, project):
        raise NotImplementedError

    def slug_refusal(self, project):
        """A message when the Manifest's slug is one the host keeps, or None."""
        return None

    def stack_names(self, project):
        """What the file's `name` may render as."""
        return (SLUG,)

    def hosts(self, project):
        """The hostnames a router may answer for, as the checker renders them."""
        return (HOST,)

    def more_service_refusals(self, project, name, definition):
        return ()

    # -- the rules' messages --------------------------------------------

    def refuse(self, rule_id, messages):
        prefix = self.place + " refuses this " + self.who + ": "
        for message in messages:
            yield Violation(rule_id, prefix + message)

    def file(self, project):
        for index in other_files(project.compose_source or ""):
            yield ("docker-compose.prod.yml line " + str(index) + " brings in "
                   "another file, by include or by extends. " + self._place()
                   + " renders this file alone, so it finds no other: write "
                   "the Stack in this one.")
        refusal = self.slug_refusal(project)
        if refusal:
            yield refusal
        rendered = project.rendered or {}
        for key in sorted(rendered):
            if key not in ALLOWED_TOP:
                yield ("the file has a top-level " + key + ", and " + self.place
                       + " reads " + ", ".join(ALLOWED_TOP) + " and nothing else. "
                       "What it does not name it refuses, so a key Compose learns "
                       "later is refused until somebody reads it.")
        if rendered.get("name") not in self.stack_names(project):
            yield ("the file's name is " + show(str(rendered.get("name"))) + ", and "
                   + self.place + " deploys a " + self.who + " as its slug alone: "
                   "name: ${PROJECT_SLUG:?}.")
        if not rendered.get("services"):
            yield "the file declares no service: a " + self.who + " runs at least one."

    def networks(self, project):
        """What a network may set: its name, external, an empty ipam, and internal for its own.

        An external network under another key, and a network of the Stack's
        own given a name, are the `networks` rule's. `web` or `data`,
        external and named otherwise, is not, because that rule reads the
        key and the host the name.
        """
        stack = self._stack(project)
        for key, definition in sorted(((project.rendered or {}).get("networks") or {}).items()):
            kind = self._network_kind(stack, key, definition)
            if kind is None:
                if key in ("web", "data") and definition.get("external") is True:
                    yield ("the network " + key + " is external and named "
                           + show(str(definition.get("name"))) + ". " + self._place()
                           + " knows a network by its name: leave web and data "
                           "unnamed, as external: true alone.")
                continue  # otherwise `networks` says it: another Stack's, or named
            allowed = NETWORK_KEYS + (("internal",) if kind == "own" else ())
            for field in sorted(definition):
                if field not in allowed:
                    yield ("the network " + key + " sets " + field + ". A network a "
                           + self.who + " makes is plain: no driver, no options, no "
                           "address, not attachable; internal, if it is its own.")
            if definition.get("ipam"):
                yield ("the network " + key + " sets ipam, which chooses its "
                       "addresses: " + self.place + " gives a " + self.who
                       + "'s networks Docker's own.")

    def volumes(self, project):
        """A volume of the Stack's, named after it, on the local driver, without options.

        `driver_opts` can bind a directory of the host, and an external
        volume or one named in the file can be another Stack's.
        """
        stack = self._stack(project)
        for key, definition in sorted(((project.rendered or {}).get("volumes") or {}).items()):
            for field in sorted(definition):
                if field not in VOLUME_KEYS:
                    yield ("the volume " + key + " sets " + field + ": a " + self.who
                           + "'s volume is declared empty, {}.")
            if definition.get("external") is True:
                yield ("the volume " + key + " is external, and could be another "
                       "Stack's. Leave it unnamed and not external: Compose makes it "
                       "for this Stack.")
            if definition.get("driver_opts") is not None:
                yield ("the volume " + key + " sets driver_opts, which can bind a "
                       "directory of the host into a " + self.who + ".")
            if (definition.get("driver") or "local") != "local":
                yield ("the volume " + key + " uses the driver "
                       + str(definition.get("driver")) + ": only the local one.")
            if definition.get("external") is not True and definition.get("name") != stack + "_" + key:
                yield ("the volume " + key + " is named " + show(str(definition.get("name")))
                       + ". Leave it unnamed: Compose names it after the Stack, and a "
                       "name written in the file is the same volume for every Stack "
                       "that copies it.")

    def services(self, project):
        """No privilege, no host file, no root, no reserved name, networks joined plainly.

        The host, through the deploy user, is root: anything Compose can ask
        of it and the list does not name - privileged, a capability, a
        device, the host's network or namespaces, pull_policy - is refused.
        A service named like one of the host's own containers would be a
        second answer for it, by Docker's DNS, on any network both join. An
        alias would do the same, which is why a network is joined plainly.
        """
        declared = (project.rendered or {}).get("volumes") or {}
        for name, definition in project.services:
            if name in self.reserved_services:
                yield ("service " + name + " is named like one of " + self.host_own
                       + ", and would answer for it by Docker's DNS on every network "
                       "both join. Name it otherwise.")
            for key in sorted(definition):
                if key not in ALLOWED_SERVICE_KEYS and key not in KEYS_OF_OTHER_RULES:
                    yield ("service " + name + " sets " + key + ", which " + self.place
                           + " does not allow: a " + self.who + "'s service configures "
                           "its own process, or narrows what it may do, and asks the "
                           "host for nothing.")
            user = definition.get("user")
            if user is not None and any(_ROOT.match(part) for part in str(user).split(":")):
                yield ("service " + name + " asks to run as root (user " + str(user)
                       + "). A " + self.who + " runs as the user its image declares, "
                       "or another that is not root.")
            joined = definition.get("networks") or {}
            if not joined:
                yield ("service " + name + " joins no network: a " + self.who + "'s "
                       "service joins web, data with the database Add-on, or a "
                       "network of the Stack's own.")
            for network, settings in sorted(joined.items()):
                if settings:
                    yield ("service " + name + " joins " + network + " with "
                           + ", ".join(sorted(settings)) + ". A " + self.who + " joins "
                           "a network plainly, without aliases or addresses: an alias "
                           "is a name that answers for every Stack on that network.")
            for mount in definition.get("volumes") or []:
                kind = mount.get("type") if isinstance(mount, dict) else None
                if kind == "volume" and mount.get("source") not in declared:
                    # Not declared and named: Compose refuses to render it,
                    # which `compose-file` says. So this is an anonymous one.
                    yield ("service " + name + " mounts an anonymous volume at "
                           + str(mount.get("target")) + ". Only a volume the file "
                           "declares is the Stack's: declare it under volumes: and "
                           "mount it by its key.")
                elif kind not in ("volume", "tmpfs"):
                    yield ("service " + name + " mounts a " + str(kind) + " ("
                           + show(str(mount.get("source", "") if isinstance(mount, dict) else mount))
                           + "): nothing of the host is mounted into a " + self.who
                           + ", only a volume of its Stack, or a tmpfs.")
            yield from self.more_service_refusals(project, name, definition)

    def labels(self, project):
        """A Stack answers for its own hostname, through routers, services and middlewares of its own.

        Its own: the slug, or the slug, a separator and a suffix. Its rule:
        Host of its hostname, optionally with one Path or PathPrefix. Its
        entrypoints, tls and certresolver: the host's. Nothing else of
        Traefik's, and none of Docker Compose's own labels, which say whose
        container this is.
        """
        for name, definition in project.services:
            labels = compose.labels(definition)
            for message in self._label_refusals(project, labels):
                yield "service " + name + ": " + message
            routers = sorted({m.group(1) for k in labels for m in [_ROUTER.match(k.lower())] if m})
            for router in routers:
                if "traefik.http.routers." + router + ".rule" not in labels:
                    yield ("service " + name + ": router " + show(router) + " has no "
                           "rule, and Traefik would make one up.")
            if labels.get("traefik.enable") == "true" and not routers:
                yield ("service " + name + ": traefik.enable is true and it declares "
                       "no router, so Traefik would make one up, answering for a name "
                       "of its own.")

    # -- the pieces ------------------------------------------------------

    def _place(self):
        return self.place[0].upper() + self.place[1:]

    def _stack(self, project):
        """The name Compose gives this Stack's own networks and volumes, before `_<key>`."""
        name = (project.rendered or {}).get("name")
        return name if name in self.stack_names(project) else SLUG

    def _network_kind(self, stack, key, definition):
        """web, data, the Stack's own, or None, as compose-policy.jq's net_kind."""
        if definition.get("external") is not True:
            return "own" if definition.get("name") == stack + "_" + key else None
        if definition.get("name") == "web":
            return "web"
        if definition.get("name") in self.data_names:
            return "data"
        return None

    def _own(self, name):
        """A Traefik name of this Stack's: the slug, or the slug, a separator and more."""
        return bool(_NAME.match(name)) and (
            name == SLUG
            or any(name.startswith(SLUG + s) and len(name) > len(SLUG) + 1 for s in self.separators)
        )

    def _own_ref(self, name):
        return self._own(name[: -len("@docker")] if name.endswith("@docker") else name)

    def _not_own(self, kind, name):
        names = ["${PROJECT_SLUG}" + s + "..." for s in self.separators]
        written = ", ".join(["${PROJECT_SLUG}"] + names[:-1]) + " or " + names[-1]
        return (kind + " " + show(name) + " is not named after the slug (" + written
                + "), so another " + self.who + " could have it" + self.not_own_why + ".")

    def _rule(self, project):
        hosts = "|".join(re.escape(h) for h in self.hosts(project))
        return re.compile(
            r"^Host\(`(" + hosts + r")`\)( && (Path|PathPrefix)\(`/[A-Za-z0-9._~/-]*`\))?$"
        )

    def _label_refusals(self, project, labels):
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
                    yield "label " + key + " is " + value + ": Traefik reaches a " + self.who + " on web."
            elif lower.startswith(("traefik.http.routers.", "traefik.http.services.")):
                if key != lower:
                    yield "label " + show(key) + ": a router's or a service's labels are written in lower case."
                elif lower.startswith("traefik.http.routers."):
                    yield from self._router_label(project, key, value)
                else:
                    yield from self._service_label(key, value)
            elif lower.startswith("traefik.http.middlewares."):
                yield from self._middleware_label(key)
            else:
                yield ("label " + show(key) + " is not allowed: a " + self.who + " has "
                       "http routers, services and middlewares of its own, and "
                       "nothing else of Traefik's.")

    def _router_label(self, project, key, value):
        found = _ROUTER.match(key)
        if found is None:
            yield "label " + show(key) + " is not a router's."
            return
        name, part = found.groups()
        if not self._own(name):
            if SLUG in name:
                yield self._not_own("router", name)
            return  # a literal: `traefik-names`
        if part == "rule":
            if not self._rule(project).match(value):
                yield ("router " + show(name) + " has the rule " + show(value)
                       + ": a router of a " + self.who + " answers for "
                       "Host(`${APP_HOST}`), optionally with one Path or PathPrefix.")
        elif part == "entrypoints":
            if any(entry.strip() not in ("web", "websecure") for entry in value.split(",")):
                yield "router " + show(name) + " uses the entrypoints " + value + ": web and websecure are the only ones."
        elif part == "middlewares":
            for used in (entry.strip() for entry in value.split(",")):
                if not (self._own_ref(used) or used in self.file_middlewares):
                    yield "router " + show(name) + " uses the middleware " + show(used) + ", which is not its own."
        elif part == "service":
            if not self._own_ref(value):
                yield "router " + show(name) + " points at the service " + show(value) + ", which is not its own."
        elif part == "priority":
            if not re.match(r"^[0-9]{1,6}$", value):
                yield "router " + show(name) + " has the priority " + value + ": a whole number, of six digits at most."
        elif part == "tls":
            if value != "true":
                yield "router " + show(name) + " has tls " + value + ": true, or no tls label."
        elif part == "tls.certresolver":
            if value != "letsencrypt":
                yield ("router " + show(name) + " uses the certresolver " + value
                       + ": " + self.place + " has letsencrypt, one certificate per hostname.")
        else:
            yield "label " + show(key) + " is not allowed " + self.on_place + "."

    def _service_label(self, key, value):
        found = _SERVICE.match(key)
        if found is None:
            yield "label " + show(key) + " is not a service's."
            return
        name, part = found.groups()
        if not self._own(name):
            if SLUG in name:
                yield self._not_own("Traefik service", name)
            return
        if part == "loadbalancer.server.port":
            if not re.match(r"^[0-9]{1,5}$", value):
                yield "Traefik service " + show(name) + " has the port " + value + ": the number the container listens on."
        elif part == "loadbalancer.server.scheme":
            if value not in ("http", "https", "h2c"):
                yield "Traefik service " + show(name) + " has the scheme " + value + ": http, https or h2c."
        else:
            yield "label " + show(key) + " is not allowed " + self.on_place + "."

    def _middleware_label(self, key):
        """Read in lower case, as the policy reads it: its labels' case is free."""
        found = _MIDDLEWARE.match(key.lower())
        if found is None:
            yield "label " + show(key) + " is not a middleware's."
            return
        name, kind = found.groups()
        if not self._own(name):
            # A literal is `traefik-names`'s, which reads the label as
            # written: one it cannot read, in another case, is said here.
            if SLUG in name or not key.startswith("traefik.http.middlewares."):
                yield self._not_own("middleware", name)
            return
        if kind not in MIDDLEWARE_TYPES:
            yield "middleware " + show(name) + " is a " + kind + ", which is not allowed " + self.on_place + "."
