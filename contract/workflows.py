"""A workflow that runs a branch's code holds no secret (decision 85 of phase 6).

The agent of badge 2 can push a branch and open a pull request in a Project,
and nothing else that matters: it cannot edit `.github/workflows/`, merge,
or tag `v*`. But it edits what the workflows run - the scripts, the
Dockerfiles, the tests - so a workflow that runs a branch's code runs the
agent's code, with whatever that workflow holds. A secret there is a secret
the branch can send anywhere; `packages: write` there lets the branch push
over an image tag a rollback pulls, since GHCR tags can be overwritten. So
such a workflow names no secret but `GITHUB_TOKEN`, and is granted no write.

The workflows are read with `yaml_subset`, the checker's own reader, which
refuses what it does not implement; a workflow it cannot read is refused
here too, since what it cannot read it cannot say holds no secret.
"""

import re

from . import yaml_subset
from .rules import Violation, rule

RULE = "branch-code-holds-no-secret"

# Where GitHub reads workflows from: this directory, not below it.
WORKFLOWS = ".github/workflows/"

# The events that run a branch's code whatever the branch: a pull request's
# head merged into its base, or the branch just created. Not `pull_request`
# alone: the agent may review and comment on a pull request, and each of
# those runs the same code as `pull_request` does, with the same secrets.
BRANCH_EVENTS = (
    "pull_request", "pull_request_review", "pull_request_review_comment", "create",
)

# The one branch only a merge reaches, and the tags only the operator makes.
TRUSTED_BRANCH = "main"
TRUSTED_TAG_PREFIX = "v"

# The triggers whose checkout ref is not read: the events above are a
# branch's code whatever they check out, a push is judged by its filters,
# and `workflow_dispatch` is the operator's (`branch_code` says why).
CHECKOUT_EXEMPT = BRANCH_EVENTS + ("push", "workflow_dispatch")

# A checkout ref that names the default branch on every other trigger -
# `repository_dispatch`, `workflow_run`, `issue_comment`, `schedule` -
# whose GITHUB_SHA and GITHUB_REF are the default branch's head. Compared
# with the spaces removed.
DEFAULT_REFS = (
    "main", "refs/heads/main",
    "${{github.event.repository.default_branch}}",
    "refs/heads/${{github.event.repository.default_branch}}",
    "${{github.sha}}", "${{github.ref}}", "${{github.ref_name}}",
)

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_SECRETS = re.compile(r"(?<![\w.-])secrets\b", re.I)
_SECRET_NAME = re.compile(r"\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)|\[\s*'([^']*)'\s*\])")
# `github.event` whole, as in toJSON(github.event): not `.x` of it, nor event_name.
_WHOLE_EVENT = re.compile(r"github\s*\.\s*event\b(?!\s*[.\[])")


def files(project):
    return [
        name for name in project.files
        if name.startswith(WORKFLOWS)
        and "/" not in name[len(WORKFLOWS):]
        and name.endswith((".yml", ".yaml"))
    ]


def triggers(workflow):
    """The events a workflow runs on, each with its configuration or None."""
    on = workflow.get("on")
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(event): None for event in on}
    if isinstance(on, dict):
        return dict(on)
    return {}


def jobs(workflow):
    found = workflow.get("jobs")
    if not isinstance(found, dict):
        return []
    return [(name, job) for name, job in found.items() if isinstance(job, dict)]


def steps(job):
    found = job.get("steps")
    if not isinstance(found, list):
        return []
    return [
        (step.get("name") or "step " + str(index), step)
        for index, step in enumerate(found, start=1)
        if isinstance(step, dict)
    ]


def _push(config):
    """Why a `push` trigger runs a branch's code: a phrase per reason, or none."""
    config = config if isinstance(config, dict) else {}
    filters = ("branches", "branches-ignore", "tags", "tags-ignore")
    if not any(key in config for key in filters):
        return ["on a push with no branches: or tags: filter, which runs on every branch"]
    reasons = []
    if "branches-ignore" in config:
        reasons.append("on a push filtered by branches-ignore:, which runs on every branch it does not name")
    elif "branches" in config:
        others = _outside(config["branches"], lambda p: p == TRUSTED_BRANCH)
        if others:
            reasons.append("on a push to " + ", ".join(others) + ", not only to " + TRUSTED_BRANCH)
    if "tags-ignore" in config:
        reasons.append("on a push filtered by tags-ignore:, which runs on every tag it does not name")
    elif "tags" in config:
        others = _outside(config["tags"], lambda p: p.startswith(TRUSTED_TAG_PREFIX))
        if others:
            reasons.append("on a push of the tags " + ", ".join(others)
                           + ", not only " + TRUSTED_TAG_PREFIX + "*")
    return reasons


def _outside(patterns, trusted):
    """The patterns of a filter that reach past what is trusted. `!` only narrows."""
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not patterns:
        return ["a filter naming nothing"]
    return [
        str(pattern) for pattern in patterns
        if not (isinstance(pattern, str) and (pattern.startswith("!") or trusted(pattern)))
    ]


def _uses(step, action):
    uses = step.get("uses")
    return isinstance(uses, str) and uses.lower().startswith(action + "@")


def _checkouts(workflow):
    """(job, ref) for every `actions/checkout` step naming a ref."""
    for job_name, job in jobs(workflow):
        for _, step in steps(job):
            if not _uses(step, "actions/checkout"):
                continue
            given = step.get("with")
            ref = given.get("ref") if isinstance(given, dict) else None
            if ref is not None:
                yield job_name, str(ref)


def branch_code(workflow):
    """Why a workflow runs a branch's code: one phrase per reason, none if it does not.

    `workflow_dispatch` is not a reason. It runs whatever ref it is
    dispatched on, and `build.yml`'s holds `packages: write`, but
    dispatching needs Actions write, which neither App holds: only the
    operator chooses that ref, as only the operator merges.
    """
    on = triggers(workflow)
    reasons = ["on " + event for event in BRANCH_EVENTS if event in on]
    if "push" in on:
        reasons.extend(_push(on["push"]))
    others = [event for event in on if event not in CHECKOUT_EXEMPT]
    if others:
        for job_name, ref in _checkouts(workflow):
            if re.sub(r"\s+", "", ref) not in DEFAULT_REFS:
                reasons.append("on " + ", ".join(others) + ", where job "
                               + job_name + " checks out " + ref)
    return reasons


def _injected(text):
    """What of the event a text pastes in through ${{ }}, as phrases."""
    found = []
    for expression in _EXPRESSION.findall(text):
        if "client_payload" in expression:
            found.append("github.event.client_payload")
        elif _WHOLE_EVENT.search(expression):
            found.append("the whole github.event")
    return sorted(set(found))


def _expressions(node, key=None):
    """Every expression of a workflow: what each ${{ }} holds, and an if: whole."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _expressions(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _expressions(value, key)
    elif isinstance(node, str):
        if key == "if":
            yield node
        else:
            for found in _EXPRESSION.finditer(node):
                yield found.group(1)


def secrets(workflow):
    """The secrets a workflow names, `GITHUB_TOKEN` aside."""
    names = set()
    for expression in _expressions(workflow):
        for found in _SECRETS.finditer(expression):
            named = _SECRET_NAME.match(expression, found.end())
            name = (named.group(1) or named.group(2)) if named else None
            if name is None:
                names.add("the whole secrets context")
            elif name.upper() != "GITHUB_TOKEN":
                names.add(name)
    return sorted(names)


def _written(permissions):
    """The scopes a `permissions:` value grants write to."""
    if isinstance(permissions, str):
        return ["every scope, by write-all"] if permissions == "write-all" else []
    if isinstance(permissions, dict):
        return sorted(scope for scope, level in permissions.items() if level == "write")
    return []


def _has_permissions(node):
    return node.get("permissions") is not None


@rule(RULE, "a workflow that runs a branch's code holds no secret and writes nothing")
def branch_code_holds_no_secret(project):
    """A branch's code runs with what its workflow holds (decision 85 of phase 6).

    A branch's code runs on `pull_request` and the events like it, on a
    `push` not limited to `main` or to `v*` tags - with no filter, on every
    branch - and on any other trigger but `workflow_dispatch` whose
    checkout names a ref other than the default branch: a
    `repository_dispatch` naming the payload's, a `workflow_run` naming
    its head_sha, an `issue_comment` naming a pull request's head. Such a workflow names no secret but
    `GITHUB_TOKEN`, passes none on with `secrets: inherit`, and grants no
    write, at its level or a job's. It must say so: with no `permissions:`
    key, the token gets the repository's default, which may be write.

    In every workflow: `pull_request_target` is refused, since it runs with
    the secrets and a write token beside a pull request's code as soon as a
    step checks it out; `github.event.client_payload`, or `github.event`
    whole, is refused inside a `run:` block or `actions/github-script`'s
    `script:`, where it is pasted into the code as text the sender of the
    event chose - it goes through `env:`, and the script checks it; and a
    checkout whose `with:` is one expression is refused, since its ref
    cannot be read. A ref a script fetches itself, from `env:`, is unseen.

    The Template's `lab-redeploy.yml` runs on `repository_dispatch` with the
    lab's key, and passes: it checks out the default branch, and refuses a
    payload naming any other commit. `workflow_dispatch` is not a branch's
    code: `branch_code` says why.
    """
    for name in files(project):
        text = project.text(name)
        if text is None:
            continue
        try:
            workflow = yaml_subset.load(text)
        except yaml_subset.YamlError as exc:
            yield Violation(
                RULE,
                name + " could not be read, " + str(exc) + ". The checker "
                "reads workflows itself and refuses what it does not "
                "implement rather than guess, and a workflow it cannot read "
                "is one it cannot say holds no secret. Write it without that "
                "construct: an anchor's content is written out where it is "
                "used.",
            )
            continue
        if workflow is None:
            continue
        if not isinstance(workflow, dict):
            yield Violation(
                RULE,
                name + " is not a mapping of keys, so it is not a workflow "
                "GitHub would run, and the checker cannot say what it holds.",
            )
            continue
        yield from _everywhere(name, workflow)
        reasons = branch_code(workflow)
        if reasons:
            yield from _branch_code(name, workflow, reasons)


def _everywhere(name, workflow):
    """What is refused in any workflow, whatever it runs on."""
    if "pull_request_target" in triggers(workflow):
        yield Violation(
            RULE,
            name + " runs on pull_request_target, which holds the "
            "repository's secrets and a token that can write while a pull "
            "request's code is one checkout away. Use pull_request, which "
            "this rule keeps holding neither.",
        )
    for job_name, job in jobs(workflow):
        for step_name, step in steps(job):
            where = name + ": job " + job_name + ", " + step_name + ", "
            given = step.get("with")
            code = [("its run: block", step.get("run"))]
            if _uses(step, "actions/github-script") and isinstance(given, dict):
                code.append(("the script: of actions/github-script", given.get("script")))
            for place, text in code:
                if not isinstance(text, str):
                    continue
                for pasted in _injected(text):
                    yield Violation(
                        RULE,
                        where + "writes " + pasted + " into " + place + ". An "
                        "expression there is pasted into the code before it "
                        "runs, so whoever sent the event writes code. Pass "
                        "it through env:, as VALUE: ${{ "
                        "github.event.client_payload.value }}, and check "
                        "the value in the code that reads it.",
                    )
            if _uses(step, "actions/checkout") and isinstance(given, str):
                yield Violation(
                    RULE,
                    where + "gives actions/checkout its with: as one "
                    "expression, so the ref it checks out cannot be read "
                    "here, and may be one a branch chose. Write ref: as a "
                    "key of its own.",
                )


def _branch_code(name, workflow, reasons):
    """What a workflow running a branch's code may not hold."""
    runs = name + " runs a branch's code (" + "; ".join(reasons) + ")"

    named = secrets(workflow)
    if named:
        yield Violation(
            RULE,
            runs + " and names " + ", ".join(named) + ". A branch's code "
            "runs beside what its workflow holds, and the agent can push a "
            "branch, so a secret here is one the branch can send anywhere. "
            "Only GITHUB_TOKEN may be named; a secret belongs to a workflow "
            "that runs reviewed code, on a push to main or a v* tag.",
        )
    for job_name, job in jobs(workflow):
        if job.get("secrets") == "inherit":
            yield Violation(
                RULE,
                runs + " and job " + job_name + " passes secrets: inherit, "
                "which hands the workflow it calls every secret of the "
                "repository. Pass none.",
            )

    unset = [job_name for job_name, job in jobs(workflow) if not _has_permissions(job)]
    if not _has_permissions(workflow) and unset:
        yield Violation(
            RULE,
            runs + " and has no permissions: key, at the top of the file or "
            "in job " + ", ".join(unset) + ", so the token gets the "
            "repository's default, which may be write. Write permissions: "
            "at the top of the file, with contents: read.",
        )

    grants = [("the workflow's level", _written(workflow.get("permissions")))]
    grants += [("job " + job_name, _written(job.get("permissions")))
               for job_name, job in jobs(workflow)]
    for where, scopes in grants:
        if scopes:
            yield Violation(
                RULE,
                runs + " and grants write to " + ", ".join(scopes) + " at "
                + where + ". GHCR tags can be overwritten, so packages: "
                "write would let a branch replace an image a rollback pulls; "
                "no write is safe beside code nobody has reviewed. Grant "
                "read: packages: read is enough to pull.",
            )
