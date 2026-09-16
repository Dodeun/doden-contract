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
sentinel does. Every other variable the file interpolates is removed from the
environment, so the same tree renders the same way in CI and on a laptop where
one of those names happens to be exported.
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
}

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
    environment = dict(os.environ)
    for use in variable_uses(source):
        environment.pop(use.name, None)
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
            "render with only " + ", ".join(IDENTITY_VARIABLES) + " set: "
            + " ".join(detail[-3:] if detail else ["no output"])
        )

    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, "docker compose config emitted no usable JSON: " + str(exc)


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
