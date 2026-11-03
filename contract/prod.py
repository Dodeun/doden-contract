"""What production refuses, read from the repository (ticket 77 of phase 5).

A Project is deployed by `prod-deploy`, the forced command of its own deploy
key on the production host, which renders the Compose file in a sandbox and
holds the render to a list: `prod-deploy/compose-policy.jq` in `doden-infra`
(decisions 63 to 65). The deploy user is in the `docker` group, which is
root, and the file was written by whoever could push a tag, so the list
names what a Project may ask for and refuses the rest. These rules are that
list, read the same way from the same render, so a Project learns what
production refuses on its pull request, and not at its deploy, after a
release tag that cannot be moved.

**A Project only**, a Manifest with no `prototype` block. A Prototype never
reaches production - `prod-deploy` refuses its Manifest - and is held to the
lab's list, `lab.py`'s rules. A promoted Prototype moves from one to the
other.

**The lab's list, with decision 64's four differences**, so most of the
reading is `allowlist.py`'s, and what differs is here:

1. `data` keeps its name; the lab's is `lab-data`.
2. Router, service and middleware names are the slug, or the slug, then `-`
   or `_` and a suffix: both live Projects name theirs `<slug>-api`. A
   person chooses every slug, and `prod-pin-key` refuses one that begins
   another's followed by `-`.
3. A router answers for Host of the key's hostname. Tier 1 cannot see the
   key; it reads the Manifest's `appHost`, which `prod-deploy` refuses
   unless it is the key's. So a literal hostname is accepted when it is the
   Manifest's, as a literal slug is in the file's `name`.
4. Every image is `ghcr.io/dodeun/<slug>-<name>` at the request's commit,
   so a stolen key alone cannot start an attacker's image.

And no `@file` middleware: production's Traefik has no file provider.

**One copy of the list, two readers.** docs/prod.md is the table of every
refusal of the host's list beside the rule here that catches it, or the
reason none can; tests/test_prod.py fails when a row of the table has no
test.
"""

import re

from . import compose
from .allowlist import SLUG, AllowList, show
from .rules import Violation, rule

PREFIX = compose.SENTINEL["IMAGE_REPO_PREFIX"]
TAG = compose.SENTINEL["IMAGE_TAG"]

# The host's own containers: a service of one of these names would be a
# second answer for it, by Docker's DNS, on any network both join. A service
# called `postgres` on `data` would receive every other Project's database
# password.
RESERVED_SERVICES = ("traefik", "postgres", "placeholder", "maintenance")

# The host's own Compose projects and the databases every engine has, which
# prod-deploy and prod-pin-key refuse as a slug (decision 70).
RESERVED_SLUGS = ("postgres", "template0", "template1", "traefik", "placeholder", "maintenance")
RESERVED_SLUG_PREFIX = "pg-"

# A service name `docker compose run` cannot read as an option: the migrate
# command names its service to it.
_SERVICE_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*")

# What bash, the loader, docker, Compose or prod-deploy itself reads from its
# environment. A secret of one of these names would be read by the deploy,
# which hands docker the Stack's secrets by name.
_SECRET_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
FORBIDDEN_SECRETS = (
    "PATH", "HOME", "IFS", "ENV", "BASH_ENV", "BASHOPTS", "SHELLOPTS", "PS4",
    "TMPDIR", "GCONV_PATH", "USER", "SHELL", "LOGNAME", "SSH_ORIGINAL_COMMAND",
    "PROJECT_SLUG", "APP_HOST", "IMAGE_REPO_PREFIX", "IMAGE_TAG", "RELEASE_TAG",
)
FORBIDDEN_SECRET_PREFIXES = (
    "BASH_FUNC_", "LD_", "DOCKER_", "COMPOSE_", "BUILDKIT_", "DOPPLER_", "PROD_DEPLOY_",
)

# What the lab sets on a Preview, and a Project reads to open its demo
# sign-in (decision 93). Refused with a value and without one: a key with no
# value is a secret Doppler fills, so refusing `=preview` alone would leave
# that path open. And in any case: Node reads it exactly, but some
# configuration libraries match names without case (pydantic-settings by
# default), so `doden_environment` could open the same route.
PREVIEW_ONLY = "DODEN_ENVIRONMENT"

# What `preview start` sets on a Preview itself, beside the names above:
# the lab's database, the switch, and the key the agent calls it with
# (decision 50). A generated value of one of these names would be replaced,
# or would replace it.
PREVIEW_SETS = ("DATABASE_URL", PREVIEW_ONLY, "APP_API_KEY")
# What the lab's deploy reads besides production's list: a Preview is
# rendered and started there, and lab-deploy.sh refuses these as a secret.
PREVIEW_RESERVED_PREFIXES = FORBIDDEN_SECRET_PREFIXES + ("LAB_DEPLOY_", "LAB_PREVIEW_")


class Production(AllowList):
    place = "production"
    on_place = "in production"
    who = "Project"
    host_own = "the production host's own containers"
    reserved_services = RESERVED_SERVICES
    data_names = ("data",)
    separators = ("-", "_")

    def applies(self, project):
        return project.manifest is not None and project.prototype is None

    def slug_refusal(self, project):
        slug = project.slug
        if slug in RESERVED_SLUGS or slug.startswith(RESERVED_SLUG_PREFIX):
            return ("the slug " + slug + " is a name the production host keeps for "
                    "itself - " + ", ".join(RESERVED_SLUGS) + ", and anything "
                    "beginning " + RESERVED_SLUG_PREFIX + " - one of its own Compose "
                    "projects or databases, and production deploys no Project under "
                    "it. A Project is named otherwise.")
        return None

    def stack_names(self, project):
        return (SLUG, project.slug)

    def hosts(self, project):
        return super().hosts(project) + (project.manifest["appHost"],)

    def more_service_refusals(self, project, name, definition):
        """What production refuses and the lab does not: names, images, mounts, secrets."""
        if not _SERVICE_NAME.fullmatch(name):
            yield ("service " + name + " has a name production refuses: a service is "
                   "named in lower case, letters, digits, `_`, `.` and `-`, starting "
                   "with a letter or a digit, so that `docker compose run` cannot read "
                   "it as an option.")
        image = definition.get("image")
        if self._image_is_tagged(image) and not self._own_image(project, image):
            yield ("service " + name + " runs the image " + show(image) + ": a Project "
                   "runs its own images, ${IMAGE_REPO_PREFIX}-<name>:${IMAGE_TAG}, which "
                   "production pulls from ghcr.io/dodeun/<slug>-<name> at the commit it "
                   "deploys, and no other: a stolen deploy key must not be able to "
                   "start somebody else's image.")
        declared = (project.rendered or {}).get("volumes") or {}
        for mount in definition.get("volumes") or []:
            if not isinstance(mount, dict) or mount.get("type") != "volume":
                continue
            if mount.get("source") not in declared:
                continue  # an anonymous volume, which allowlist.py says
            options = sorted(set(mount.get("volume") or {}) - {"nocopy"})
            if options:
                yield ("service " + name + " mounts the volume " + str(mount.get("source"))
                       + " with " + ", ".join(options) + ": a volume is mounted whole, "
                       "with nocopy at most, and production reads no other option.")
        for key, value in sorted((definition.get("environment") or {}).items()):
            if key.upper() == PREVIEW_ONLY:
                yield ("service " + name + " sets " + key + ", which the lab "
                       "sets on a Preview alone, to open its demo sign-in: production "
                       "refuses it with a value or without one, so no Release can "
                       "carry it.")
                continue
            if value is not None:
                continue
            if not _SECRET_NAME.fullmatch(key):
                yield ("service " + name + " takes " + key + " from Doppler, which is "
                       "not the name of an environment variable: letters, digits and "
                       "`_`, not starting with a digit.")
            elif key in FORBIDDEN_SECRETS or key.startswith(FORBIDDEN_SECRET_PREFIXES):
                yield ("service " + name + " takes " + key + " from Doppler, a name "
                       "the deploy itself reads: docker, bash or prod-deploy would act "
                       "on it, as a member of the docker group. A secret is for the "
                       "Stack: name it otherwise.")

    @staticmethod
    def _image_is_tagged(image):
        """An image `image-tag` passes: at IMAGE_TAG, or by digest. The others are that rule's."""
        if not image:
            return False
        last = image.rsplit("/", 1)[-1]
        return "@" in last or (":" in last and last.rsplit(":", 1)[1] == TAG)

    @staticmethod
    def _own_image(project, image):
        """Under ghcr.io/dodeun/<slug>-, at the commit: as rendered here, or written as the Manifest has it."""
        prefixes = [re.escape(PREFIX)] + [
            r"ghcr\.io/dodeun/" + re.escape(slug) for slug in (SLUG, project.slug)
        ]
        return bool(re.fullmatch(
            "(" + "|".join(prefixes) + r")-[a-z0-9]([a-z0-9._-]*[a-z0-9])?:" + re.escape(TAG),
            image,
        ))


PRODUCTION = Production()


@rule("prod-file", "a Project's Compose file has only what production reads", needs_render=True)
def prod_file(project):
    """The top of the file: its keys, its name, at least one service, nothing of another file.

    `include`, and `extends` naming a file, are read from the source: the
    sandbox renders the file alone, from stdin, so the other file is not
    there, or is one of the render image's own. And the slug: prod-deploy
    and prod-pin-key refuse one that is one of the host's own Compose
    projects or databases.
    """
    if PRODUCTION.applies(project):
        yield from PRODUCTION.refuse("prod-file", PRODUCTION.file(project))


@rule("prod-networks", "a Project's networks are the ones production lets a Stack make", needs_render=True)
def prod_networks(project):
    """What a network may set: its name, external, an empty ipam, and internal for its own.

    `data` is named `data`: production's is the shared Postgres's network
    itself, and the lab's `lab-data` is no network there.
    """
    if PRODUCTION.applies(project):
        yield from PRODUCTION.refuse("prod-networks", PRODUCTION.networks(project))


@rule("prod-volumes", "a Project's volumes are its own, made by the local driver", needs_render=True)
def prod_volumes(project):
    """A volume of the Stack's, named after it, on the local driver, without options."""
    if PRODUCTION.applies(project):
        yield from PRODUCTION.refuse("prod-volumes", PRODUCTION.volumes(project))


@rule("prod-services", "a Project's services ask the host for nothing production withholds", needs_render=True)
def prod_services(project):
    """The lab's, and production's own: its images, its service names, its mounts, its secrets.

    Root asked for in the file is refused, never an image's own user: an
    older Release must deploy, or a rollback is refused at the worst moment
    (decision 69). The image is the Project's own, at the commit. A secret
    of a name docker or the deploy reads would act on the deploy itself.
    And no `DODEN_ENVIRONMENT`, the Preview's switch, set or taken from
    Doppler (decision 93).
    """
    if PRODUCTION.applies(project):
        yield from PRODUCTION.refuse("prod-services", PRODUCTION.services(project))


@rule("prod-labels", "a Project's labels ask Traefik only for its own hostname and names", needs_render=True)
def prod_labels(project):
    """A Project answers for its own hostname, through routers, services and middlewares of its own.

    Its own: the slug, or the slug, `-` or `_`, and a suffix. Its hostname:
    the Manifest's `appHost`, which production holds to the key's.
    """
    if PRODUCTION.applies(project):
        yield from PRODUCTION.refuse("prod-labels", PRODUCTION.labels(project))


@rule("preview-generated", "the values a Preview generates are secrets of the Stack the platform does not set", needs_render=True)
def preview_generated(project):
    """The Manifest's `preview` block: names a Preview gets drawn at random.

    A Preview runs this Project's production Stack in the lab, against
    synthetic data, so what guards that data, a session or token signing
    key, is generated at each start and is no secret (decision 93). Each
    name is a key the Stack takes with no value: one it does not take
    would reach no container, and is a typo. None is a name the platform
    sets on a Preview itself or the deploy reads. A Prototype has no
    Preview, so no block.
    """
    if project.manifest is None:
        return  # the Manifest rule already said so
    block = project.manifest.get("preview")
    if block is None:
        return
    if project.prototype is not None:
        yield Violation(
            "preview-generated",
            'platform.json declares a "preview" block and a "prototype" block. '
            "A Preview is of a Project, never of a Prototype, which runs in the "
            "lab already: remove the preview block, or remove the prototype "
            "block by a promotion.",
        )
        return
    secrets = {
        key
        for _, definition in project.services
        for key, value in (definition.get("environment") or {}).items()
        if value is None
    }
    for name in block["generated"]:
        if name in PREVIEW_SETS or name in FORBIDDEN_SECRETS or name.startswith(PREVIEW_RESERVED_PREFIXES):
            yield Violation(
                "preview-generated",
                'the "preview" block generates ' + name + ", a name the platform "
                "sets on a Preview itself (" + ", ".join(PREVIEW_SETS) + ") or "
                "that docker, bash, production's deploy or the lab's reads. A "
                "generated value is for "
                "the Stack: name it otherwise.",
            )
        elif name not in secrets:
            yield Violation(
                "preview-generated",
                'the "preview" block generates ' + name + ", and no service of "
                "docker-compose.prod.yml takes it as a secret, a key with no "
                "value. A Preview hands the Stack its generated values through "
                "those keys alone, so this one would reach no container: "
                + (("it takes " + ", ".join(sorted(secrets)) + ".") if secrets else "it takes none."),
            )
