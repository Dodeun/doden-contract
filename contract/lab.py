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

**A Prototype only.** A Project is deployed by production, which holds it to
a list of its own, `prod.py`'s rules. The two lists share their reading,
`allowlist.py`; what is the lab's alone is here: the host's own containers,
and Traefik names joined to the slug by `_` alone. CONTRACT.md says why the
lab is stricter.

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

from .allowlist import AllowList
from .rules import rule

RESERVED_SERVICES = ("traefik", "postgres-lab", "access-code", "hello")


class Lab(AllowList):
    reserved_services = RESERVED_SERVICES
    # Not `-`: `foo-bar` is a slug too, and Prototype foo naming a service
    # `foo-bar` would join foo-bar's load balancer. No slug holds `_`.
    separators = ("_",)
    file_middlewares = ("noindex@file",)
    not_own_why = ": `-` joins two slugs, `_` cannot"

    def applies(self, project):
        return project.manifest is not None and project.prototype is not None

    def slug_refusal(self, project):
        if project.slug in RESERVED_SERVICES:
            return ("the slug " + project.slug + " is the name of one of the control "
                    "host's own containers, and the lab deploys no Prototype under "
                    "it. A Prototype is named otherwise.")
        return None


LAB = Lab()


@rule("lab-file", "a Prototype's Compose file has only what the lab reads", needs_render=True)
def lab_file(project):
    """The top of the file: its keys, its name, at least one service, nothing of another file.

    `include`, and `extends` naming a file, are read from the source, as
    `no-env-file` reads `env_file`: the render folds the other file in and
    erases the key. The lab renders this file alone, from stdin, so it
    finds no other. And the slug: lab-deploy.sh refuses one that is the name
    of one of the host's own containers.
    """
    if LAB.applies(project):
        yield from LAB.refuse("lab-file", LAB.file(project))


@rule("lab-networks", "a Prototype's networks are the ones the lab lets a Stack make", needs_render=True)
def lab_networks(project):
    """What a network may set: its name, external, an empty ipam, and internal for its own.

    `data` may be named `data` or `lab-data`: the lab renames the one to the
    other.
    """
    if LAB.applies(project):
        yield from LAB.refuse("lab-networks", LAB.networks(project))


@rule("lab-volumes", "a Prototype's volumes are its own, made by the local driver", needs_render=True)
def lab_volumes(project):
    """A volume of the Stack's, named after it, on the local driver, without options."""
    if LAB.applies(project):
        yield from LAB.refuse("lab-volumes", LAB.volumes(project))


@rule("lab-services", "a Prototype's services ask the host for nothing the lab withholds", needs_render=True)
def lab_services(project):
    """No privilege, no host file, no root, no reserved name, networks joined plainly.

    postgres-lab on lab-data, the name of a Prototype's service, would
    receive every other Prototype's database password.
    """
    if LAB.applies(project):
        yield from LAB.refuse("lab-services", LAB.services(project))


@rule("lab-labels", "a Prototype's labels ask Traefik only for its own hostname and names", needs_render=True)
def lab_labels(project):
    """A Prototype answers for its own hostname, through routers, services and middlewares of its own.

    Its own: the slug, or the slug, `_` and a suffix. The lab's
    `noindex@file` middleware, `X-Robots-Tag: noindex` on every response, is
    the one it may use besides its own.
    """
    if LAB.applies(project):
        yield from LAB.refuse("lab-labels", LAB.labels(project))
