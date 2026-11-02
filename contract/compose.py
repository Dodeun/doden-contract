"""Rendering the production Stack, and reading the source it was rendered from.

Two things live here because the contract needs both, for different reasons.

**The rendered file is what gets judged.** `docker compose config` resolves
interpolation and defaults; grepping the source would pass a Stack whose
`ports:` arrives through a variable. Rendered as JSON rather than YAML so the
standard library can read it - the checker carries no YAML parser and needs
none.

**The source is what gets judged for the rules a render erases.** Whether a
variable had a default is invisible once it has been substituted, and a
default is exactly the failure mode that matters: a Project whose
`${PROJECT_SLUG:-other-project}` nobody set starts a Compose project named
after somebody else and registers Traefik routers competing with theirs.

The render uses the platform's own values, not the Project's, and not the
invoking shell's. The Project's identity variables become fixed sentinels so
that "this name derives from the slug" can be told apart from "this name is a
literal that happens to equal the slug" - a literal does not move when the
sentinel does.

**Nothing else of the invoking shell reaches the render.** Compose fills a
key of `environment:` written with no value from the environment it runs
in, and interpolates `${...}` from it too, so a render under the caller's
environment would differ between CI, a laptop and the host: `SHELL:` would
come back filled on a runner and empty on the host. The host renders with
the five values it supplies and nothing else, so a key written with no value
stays null there unless it is one of them, or `PWD` or `COMPOSE_PROJECT_NAME`,
which Compose fills itself - and a key left null is a secret, taken from
Doppler. The checker renders the same way: the sentinels, and
only the few variables the docker CLI reads to find its configuration and
its Compose plugin. A key of one of those names that comes back with exactly
their value was filled by the render, and is put back to null, as the host
has it.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import CheckerError

COMPOSE_FILE = "docker-compose.prod.yml"

# Values the platform supplies at deploy time. A Project may not default any
# of them: a default resolves to another Project's identity rather than
# failing, and it does so silently.
IDENTITY_VARIABLES = ("PROJECT_SLUG", "APP_HOST", "IMAGE_TAG", "IMAGE_REPO_PREFIX")

# Deliberately unmistakable. Nothing a Project would write by hand, so a
# rendered name containing one of these was derived and not typed.
SENTINEL = {
    "PROJECT_SLUG": "sentinel-slug-8f3a1c",
    "APP_HOST": "sentinel-host-8f3a1c.invalid",
    "IMAGE_TAG": "sha-8f3a1c00000000000000000000000000000000",
    "IMAGE_REPO_PREFIX": "ghcr.io/sentinel-8f3a1c/sentinel",
    # Supplied by the platform too, from the release, and free to default:
    # `${RELEASE_TAG:-unknown}` names nothing of another Project's.
    "RELEASE_TAG": "v0.0.0-sentinel-8f3a1c",
}

# What the docker CLI reads to find its configuration and its plugins, on
# Linux, macOS and Windows: without them `docker compose` is not found on
# Windows. Passed to the render and to nothing else, and never one the file
# interpolates.
DOCKER_CLI_VARIABLES = (
    "HOME", "DOCKER_CONFIG", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "PROGRAMFILES", "SYSTEMROOT",
)

_BRACED = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)((?::-|:\?|:\+|-|\?|\+))?([^}]*)\}"
)
_BARE = re.compile(r"(?<![$\w])\$([A-Za-z_][A-Za-z0-9_]*)")


class Use(object):
    """One occurrence of a variable in the Compose source."""

    def __init__(self, name, operator, line):
        self.name = name
        self.operator = operator or ""
        self.line = line

    @property
    def has_default(self):
        return self.operator in ("-", ":-")

    @property
    def fails_when_unset(self):
        return self.operator in ("?", ":?")

    def __repr__(self):
        return "Use({0}, {1!r}, line {2})".format(self.name, self.operator, self.line)


def variable_uses(text):
    """Every variable the Compose source interpolates, with how it was written."""
    uses = []
    for index, line in enumerate(text.splitlines(), start=1):
        # A commented-out line is a line Compose never reads, and a rule that
        # fires on one is a rule nobody trusts the second time.
        if line.lstrip().startswith("#"):
            continue
        # `$$` is Compose's escape for a literal dollar; it interpolates nothing.
        scrubbed = line.replace("$$", "")
        for match in _BRACED.finditer(scrubbed):
            uses.append(Use(match.group(1), match.group(2), index))
        for match in _BARE.finditer(_BRACED.sub("", scrubbed)):
            uses.append(Use(match.group(1), None, index))
    return uses


def render(tree):
    """Render the production Stack. Returns (rendered, error); one is None."""
    path = Path(tree) / COMPOSE_FILE
    if not path.is_file():
        return None, (
            COMPOSE_FILE + " is missing. The production Stack is one Compose "
            "file at the root of the repository, named " + COMPOSE_FILE + "."
        )

    source = path.read_text(encoding="utf-8")
    interpolated = {use.name for use in variable_uses(source)}
    cli = {
        name: os.environ[name]
        for name in DOCKER_CLI_VARIABLES
        if name in os.environ and name not in interpolated
    }
    environment = dict(cli)
    environment.update(SENTINEL)

    handle, empty_env = tempfile.mkstemp(prefix="doden-contract-", suffix=".env")
    os.close(handle)
    try:
        proc = subprocess.run(
            [
                "docker", "compose",
                "--project-directory", str(tree),
                "--file", str(path),
                # Neutralises any .env sitting in the tree, so the verdict is
                # the same wherever the tree is checked out. A committed .env
                # is separately a violation; it must not also be able to
                # change what the other rules see.
                "--env-file", empty_env,
                "config", "--format", "json",
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
    except FileNotFoundError:
        # Not a violation: this machine cannot run the check, and the Project
        # is not what is wrong. Reporting it as a failed rule would send
        # somebody to edit a Compose file to fix a missing Docker.
        raise CheckerError(
            "docker compose is not installed. The contract is asserted "
            "against the rendered Compose file rather than its text - a Stack "
            "whose ports: arrive through a variable would otherwise pass - so "
            "there is nothing this checker can say without it."
        )
    finally:
        os.unlink(empty_env)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return None, (
            "docker compose config could not render " + COMPOSE_FILE + ", so "
            "no rule about the Stack could be checked. The platform renders "
            "this file knowing only the Manifest and the release, so it must "
            "render with only " + ", ".join(SENTINEL) + " set: "
            + " ".join(detail[-3:] if detail else ["no output"])
        )

    try:
        rendered = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, "docker compose config emitted no usable JSON: " + str(exc)
    return _unfilled(rendered, cli), None


def _unfilled(rendered, cli):
    """The render, with the keys Compose filled from the docker CLI's variables null again.

    The host renders without them, so there a key such as `HOME:` stays
    null, a secret. Only the exact value is put back: a key written with a
    value of its own keeps it.
    """
    for definition in (rendered.get("services") or {}).values():
        environment = definition.get("environment")
        if not isinstance(environment, dict):
            continue
        for name, value in cli.items():
            if environment.get(name) == value:
                environment[name] = None
    return rendered


def services(rendered):
    """The Stack's services, as (name, definition) pairs, in file order."""
    return sorted((rendered or {}).get("services", {}).items())


def labels(definition):
    """A service's labels.

    Always a map: a Compose file may write them as a list, and the render
    normalises that away before the rules ever see it. This reads the
    rendered file, so there is one form to handle rather than two.
    """
    return dict(definition.get("labels") or {})
