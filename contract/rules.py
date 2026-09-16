"""The tier-1 rules: everything the checker can see in a repository.

No rule here names a language. A rule asserting `npm ci` would reject a
legitimate Project - the language belongs to the Profile (the Template),
never to the contract.

Each rule is a function taking a `Project` and yielding `Violation`s. A rule
that yields nothing passed. Rules never raise for a condition they are meant
to judge: a missing file is a violation, not a crash.
"""

from dataclasses import dataclass

from . import compose

# A tag that can be moved names a different image tomorrow, so a Rollback to
# it is not a rollback to anything in particular (ADR-0008).
# Unset, these two produce a Stack that is silently somebody else's: an empty
# Compose project name and Traefik routers called "-api", or an image
# reference with no tag. The other two fail visibly, so a default is banned
# on all four and the error form is required on these.
REQUIRE_ERROR_FORM = ("PROJECT_SLUG", "IMAGE_TAG")

FLOATING_TAGS = {
    "latest", "main", "master", "head", "edge", "stable", "nightly",
    "dev", "develop", "staging", "prod", "production", "release",
}


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": self.message}


RULES: list = []


def rule(rule_id: str, summary: str, needs_render: bool = False):
    """Register a rule. `summary` is what the report prints beside its verdict.

    `needs_render` marks a rule that reads the rendered Compose file. When the
    render failed there is nothing for it to read, so it is reported as *not
    checked* rather than passed - a Stack nobody could parse must not come
    back looking mostly fine, and it must not come back as a dozen violations
    that all say the same thing either.
    """

    def register(fn):
        fn.rule_id = rule_id
        fn.summary = summary
        fn.needs_render = needs_render
        RULES.append(fn)
        return fn

    return register


@rule("manifest", "platform.json exists and is a valid Manifest")
def manifest(project):
    """Every other rule reads the Manifest, so it is validated first.

    ADR-0010: the Manifest is the Project's identity in a committed file, so
    that an agent which can only see the repository can still say what this
    Project is and where it deploys.
    """
    for problem in project.manifest_problems:
        yield Violation("manifest", problem)


@rule(
    "contract-version",
    "CONTRACT.md is present and pins the version of the checker running",
)
def contract_version(project):
    """The contract has to be legible without network access (ADR-0010).

    A Project carries the rules in prose, and the version that copy was
    written against. If that version and the checker disagree, the prose an
    agent is reading is not the prose being enforced - which is worse than
    having no copy at all, because it reads as authoritative.
    """
    pinned = project.contract_doc_version
    running = project.contract_major

    if pinned is None:
        if project.contract_doc is None:
            yield Violation(
                "contract-version",
                "CONTRACT.md is missing. Copy it from doden-contract at the "
                "version this Project pins, so the rules are readable here "
                "without reaching the network.",
            )
        else:
            yield Violation(
                "contract-version",
                "CONTRACT.md does not say which version it is. It needs a "
                'line reading: Contract version: `' + running + "`",
            )
    elif pinned != running:
        yield Violation(
            "contract-version",
            "CONTRACT.md pins contract " + pinned + ", but the checker "
            "running is " + running + ". Re-copy CONTRACT.md from "
            "doden-contract@" + running + ".",
        )

    declared = project.manifest.get("contractVersion") if project.manifest else None
    if declared is not None and declared != running:
        yield Violation(
            "contract-version",
            'platform.json declares contractVersion "' + declared + '", but '
            "the checker running is " + running + ". Either pin the workflow "
            "at @" + declared + " or move this Project to " + running + ".",
        )


@rule("compose-file", "the production Stack renders from the Manifest alone")
def compose_file(project):
    """Everything else about the Stack is asserted against the rendered file.

    So this rule is the one that has to fail loudly: a Stack that cannot be
    rendered is a Stack no other rule inspected, and a checker that reported
    fourteen passes on a file it never read would be worse than no checker.
    """
    if project.render_error:
        yield Violation("compose-file", project.render_error)


@rule("no-published-ports", "no service publishes a port", needs_render=True)
def no_published_ports(project):
    """Traefik owns 80 and 443 on the host, and the platform owns routing.

    The trigger for the whole contract was a Stack that bound 80 and 443 and
    would have taken the VPS down on its first deploy (ADR-0004). A Project
    that ships its own reverse proxy is a contract violation, not a
    preference.
    """
    for name, definition in project.services:
        published = definition.get("ports") or []
        for entry in published:
            yield Violation(
                "no-published-ports",
                "service " + name + " publishes " + _port(entry) + ". Nothing "
                "in a Stack binds a host port: the platform routes to it over "
                "the web network, and ports 80 and 443 belong to Traefik.",
            )


def _port(entry):
    if isinstance(entry, dict):
        published = entry.get("published")
        target = entry.get("target")
        return "port " + str(published) + " -> " + str(target)
    return "port " + str(entry)


@rule("no-build", "nothing in the production Stack builds", needs_render=True)
def no_build(project):
    """Production holds images, not source (ADR-0011).

    The host pulls what CI published. A `build:` key means the production
    machine compiles something, which is the thing ticket 03 removed and
    ADR-0011 made structural.
    """
    for name, definition in project.services:
        if definition.get("build"):
            yield Violation(
                "no-build",
                "service " + name + " has a build: key. Images are built and "
                "published by CI and pulled by tag; the production host "
                "compiles nothing.",
            )


@rule("image-tag", "every image is named by an explicit, immutable tag", needs_render=True)
def image_tag(project):
    """A Release has to name the same bytes tomorrow that it names today.

    Rollback is re-pointing the Stack at a previous Release's published
    images. A floating tag makes that a re-pull of whatever moved onto the
    tag since, which is not a rollback to anything in particular.
    """
    for name, definition in project.services:
        reference = definition.get("image")
        if not reference:
            yield Violation(
                "image-tag",
                "service " + name + " names no image.",
            )
            continue
        if "@" in reference.rsplit("/", 1)[-1]:
            continue  # pinned by digest, which is as immutable as it gets
        last = reference.rsplit("/", 1)[-1]
        if ":" not in last:
            yield Violation(
                "image-tag",
                "service " + name + " names " + reference + " with no tag, "
                "which means :latest. Name the tag explicitly - the platform "
                "supplies IMAGE_TAG, the commit the images were published "
                "under.",
            )
            continue
        tag = last.rsplit(":", 1)[1]
        if tag in FLOATING_TAGS:
            yield Violation(
                "image-tag",
                "service " + name + " names the floating tag :" + tag + ". A "
                "tag that can be moved names different bytes tomorrow, so a "
                "Release pinned to it is not pinned to anything.",
            )


@rule("healthchecks", "every service declares a healthcheck", needs_render=True)
def healthchecks(project):
    """A container that is running is not a container that is working.

    Without a healthcheck the deploy's only evidence is that Docker started
    the process, and a Stack that comes up broken looks exactly like a Stack
    that came up.
    """
    for name, definition in project.services:
        if not definition.get("healthcheck"):
            yield Violation(
                "healthchecks",
                "service " + name + " declares no healthcheck.",
            )
        elif definition["healthcheck"].get("disable"):
            yield Violation(
                "healthchecks",
                "service " + name + " disables its healthcheck.",
            )


@rule("traefik-labels", "every routed service tells Traefik how to reach it", needs_render=True)
def traefik_labels(project):
    """Routing is a platform concern, and the labels are how a Stack asks for it.

    `traefik.docker.network=web` is not optional boilerplate: a container on
    more than one network has to say which one Traefik should dial, and
    without it Traefik picks one - sometimes `data`, where it is not.
    """
    for name, definition in project.services:
        labels = compose.labels(definition)
        routed = any(key.startswith("traefik.http.") for key in labels)
        if not routed:
            continue
        if labels.get("traefik.enable") != "true":
            yield Violation(
                "traefik-labels",
                "service " + name + " carries Traefik routers but not "
                "traefik.enable=true, so Traefik ignores every one of them.",
            )
        if labels.get("traefik.docker.network") != "web":
            yield Violation(
                "traefik-labels",
                "service " + name + " does not carry "
                "traefik.docker.network=web. A service on more than one "
                "network must name the one Traefik reaches it on.",
            )


@rule("traefik-names", "router and service names derive from the Manifest's slug", needs_render=True)
def traefik_names(project):
    """A literal name is a collision with the next Project that copies the file.

    This is checked by rendering the Stack with a slug no Project could have
    written, and requiring every Traefik name to have moved with it. A name
    that is derived moves; a name that is typed does not - which is a thing
    the rendered file can be asked, and the source cannot.
    """
    sentinel = compose.SENTINEL["PROJECT_SLUG"]
    for name, definition in project.services:
        for key in sorted(compose.labels(definition)):
            declared = _traefik_component(key)
            if declared is None or sentinel in declared:
                continue
            yield Violation(
                "traefik-names",
                "service " + name + " names the Traefik component "
                + declared + " as a literal. Derive it from the Manifest's "
                "slug (${PROJECT_SLUG}) - a literal collides with the next "
                "Project that copies this file, and two routers of one name "
                "is one Project answering for another.",
            )


def _traefik_component(key):
    """The name a `traefik.http.<kind>.<name>.…` label declares, if any."""
    parts = key.split(".")
    if len(parts) < 4 or parts[0] != "traefik" or parts[1] != "http":
        return None
    if parts[2] not in ("routers", "services", "middlewares"):
        return None
    return parts[3]


@rule("networks", "the Stack joins web, and joins data only with the database Add-on", needs_render=True)
def networks(project):
    """Both networks are the platform's, and neither is the Project's to create.

    `web` is where Traefik routes. `data` is where the shared Postgres is,
    and it is a second network rather than one because every Project's
    frontend sits on `web` and a database server does not belong within reach
    of all of them (ADR-0005).
    """
    declared = (project.rendered or {}).get("networks") or {}

    for name in ("web", "data"):
        if name in declared and not declared[name].get("external"):
            yield Violation(
                "networks",
                "the " + name + " network is not declared external: true, so "
                "Compose creates a network of its own with that name. The "
                "platform owns both networks; a Stack joins them.",
            )

    if "web" not in declared:
        yield Violation(
            "networks",
            "the Stack does not join the web network, which is where Traefik "
            "routes. Declare it as an external network.",
        )
    else:
        for name, definition in project.services:
            joined = definition.get("networks") or {}
            if "web" not in joined:
                yield Violation(
                    "networks",
                    "service " + name + " does not join the web network.",
                )

    if project.manifest is None:
        return  # the Manifest rule already said so; nothing to compare against

    wants_data = project.has_addon("database")
    if "data" in declared and not wants_data:
        yield Violation(
            "networks",
            "the Stack joins the data network, but platform.json does not "
            'declare the "database" Add-on. A Stack reaches the shared '
            "Postgres because its Manifest says it has a database, or it does "
            "not reach it at all.",
        )
    if wants_data and "data" not in declared:
        yield Violation(
            "networks",
            'platform.json declares the "database" Add-on, but the Stack does '
            "not join the data network, so nothing in it can reach the shared "
            "Postgres.",
        )


@rule("identity-variables", "the values the platform supplies fail rather than guess")
def identity_variables(project):
    """A missing value must fail, not resolve to another Project.

    This is the one rule the rendered file cannot answer, because a default
    is invisible once it has been substituted. It is also the rule with the
    worst failure mode: a Project copied from another whose PROJECT_SLUG
    nobody set starts a Compose project under the original's name and
    registers Traefik routers competing with the original's - on a host
    already running them.

    Reported once per variable rather than once per occurrence. These come in
    dozens - one per label - and a rule that produces fourteen identical
    lines buries the other rules that failed beside it.
    """
    defaulted = {}
    unguarded = {}
    for use in project.variable_uses:
        if use.name not in compose.IDENTITY_VARIABLES:
            continue
        if use.has_default:
            defaulted.setdefault(use.name, set()).add(use.line)
        elif use.name in REQUIRE_ERROR_FORM and not use.fails_when_unset:
            unguarded.setdefault(use.name, set()).add(use.line)

    for name in sorted(defaulted):
        yield Violation(
            "identity-variables",
            name + " carries a default, on " + _lines(defaulted[name])
            + " of docker-compose.prod.yml. The platform supplies it from the "
            "Manifest and the release; a default resolves to another "
            "Project's value rather than failing, and does it silently.",
        )

    for name in sorted(unguarded):
        yield Violation(
            "identity-variables",
            name + " is written ${" + name + "} on " + _lines(unguarded[name])
            + " of docker-compose.prod.yml, so it renders empty when it is "
            "unset. Write ${" + name + ":?} - running this file by hand with "
            "no value should fail rather than quietly build the wrong Stack.",
        )


def _lines(numbers):
    ordered = sorted(numbers)
    if len(ordered) == 1:
        return "line " + str(ordered[0])
    return "lines " + ", ".join(str(n) for n in ordered)


@rule("no-env-file", "no service reads an env file")
def no_env_file(project):
    """Secrets are injected into the process, never written to disk (ADR-0007).

    Read from the source and not from the rendered file, which is the one
    exception to asserting the render: `docker compose config` folds an
    env_file into `environment` and erases the key, so the rendered Stack of
    a Project that reads a file on disk is indistinguishable from one that
    does not.
    """
    source = project.compose_source or ""
    for index, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("env_file:") or stripped == "env_file":
            yield Violation(
                "no-env-file",
                "docker-compose.prod.yml line " + str(index) + " uses "
                "env_file. Configuration arrives in the environment of the "
                "process that starts the Stack - no file of values is written "
                "on the host, so there is none to leak or to go stale.",
            )


@rule("no-committed-env", "no .env is committed")
def no_committed_env(project):
    """The one mistake that puts a real secret in a public place (ADR-0007).

    `.env.example` is deliberately allowed: naming the keys is how a Project
    stays legible to somebody who has none of the values.
    """
    for name in project.files:
        base = name.rsplit("/", 1)[-1]
        if base != ".env" and not base.startswith(".env."):
            continue
        if base.endswith(".example") or base.endswith(".sample"):
            continue
        yield Violation(
            "no-committed-env",
            name + " is committed. A .env in the repository is how a real "
            "secret reaches a place it can never be removed from; keep "
            ".env.example, which names the keys and holds none of them.",
        )


@rule("multi-stage-dockerfiles", "every Dockerfile is multi-stage")
def multi_stage_dockerfiles(project):
    """Size is a contract requirement rather than an optimisation (ADR-0004).

    GHCR's free-plan transfer quota is counted against every pull the host
    makes, and a single-stage image ships the build tree - compilers, dev
    dependencies, caches - to production and over that quota on every deploy.
    """
    for name in project.files:
        base = name.rsplit("/", 1)[-1]
        if base != "Dockerfile" and not base.startswith("Dockerfile."):
            if not base.endswith(".Dockerfile"):
                continue
        path = project.tree / name
        if not path.is_file():
            continue
        stages = [
            line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip().upper().startswith("FROM ")
        ]
        if len(stages) < 2:
            yield Violation(
                "multi-stage-dockerfiles",
                name + " has " + str(len(stages)) + " stage(s). Build in one "
                "stage and copy only what runs into the next, so the runtime "
                "image does not carry the toolchain that produced it.",
            )


# -- reported, never enforced ---------------------------------------------
#
# Reports produce notes. A note never changes the verdict, which is what
# keeps "the same verdict from CI and from the local one-liner" true even
# though a laptop can see images that a CI runner cannot.

REPORTS = []


def report(fn):
    REPORTS.append(fn)
    return fn


@report
def image_size(project):
    """What a pull costs, when anything here can answer that.

    ADR-0004 argues for small images from a 200 MB illustration against
    GHCR's 1 GB/month transfer quota, and the tracker's backend is 582 MB on
    disk. Those are not the same measurement - the quota counts compressed
    layers over the wire, and nobody has measured that number - so this
    enforces nothing. A ceiling set from an illustration would reject
    Projects for the wrong reason. Ticket 16 measures it.

    A repository does not contain its images, so usually there is nothing to
    weigh and this says which images would be pulled instead. Where the
    caller supplies the real registry and tag - a deploy, or a CI job that
    has just built - the local daemon is asked, and what it answers is bytes
    on disk, which is still not the number that matters.
    """
    if project.rendered is None:
        return

    import os

    prefix = os.environ.get("IMAGE_REPO_PREFIX")
    tag = os.environ.get("IMAGE_TAG")

    for name, definition in project.services:
        written = _as_written(definition.get("image") or "")
        if not (prefix and tag):
            yield name + " pulls " + written + " - not weighed (a repository "                   "holds no images; set IMAGE_REPO_PREFIX and IMAGE_TAG to "                   "weigh what is on this machine)"
            continue
        reference = (definition.get("image") or "").replace(
            compose.SENTINEL["IMAGE_REPO_PREFIX"], prefix
        ).replace(compose.SENTINEL["IMAGE_TAG"], tag)
        size = _local_image_size(reference)
        if size is None:
            yield name + " pulls " + reference + " - not on this machine, "                   "so nothing was weighed"
        else:
            yield name + " pulls " + reference + " - {0:.0f} MB on disk, "                   "which is not what a pull transfers (ticket 16 measures "                   "that)".format(size / 1e6)


def _as_written(reference):
    """A rendered image reference, with the sentinels put back as variables."""
    for name, value in compose.SENTINEL.items():
        reference = reference.replace(value, "${" + name + "}")
    return reference


def _local_image_size(reference):
    import subprocess

    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", reference],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None
