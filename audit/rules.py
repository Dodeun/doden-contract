"""The tier-2 rules: everything that is a repository setting rather than a file.

Each rule is a function taking the Project's `Settings` and the `Platform`
they are judged against, and yielding `Finding`s. A rule that yields nothing
passed.

Two things this tier does that tier 1 does not, and both follow from where it
runs. It can compare a Project against the platform's *current* state - the
contract version, the canonical document - because it runs beside them. And
it reports rather than blocks: nothing here can stop a merge, because the
ruleset is what stops a merge. The audit's job is to notice the exception
somebody added to unblock themselves and forgot, which is a thing no check
inside the Project could ever see.

GitHub layers rulesets: every active ruleset whose conditions match a ref
contributes its rules, and the ref is governed by the union. So these rules
ask what the union enforces rather than whether a ruleset of a particular
name exists. A Project that splits the same rules across two rulesets is
protected, and asking for one named `protect-main` would be auditing a filing
convention.
"""

import hashlib
from dataclasses import dataclass

# What a pull request must be judged by before it can land. `test` is the
# suite, `contract / tier-1` is this contract's own checker - the compound
# name is not a typo, GitHub names the job of a called workflow after the
# caller and the callee both. A Project requiring *more* than these is not
# drift; requiring fewer is.
REQUIRED_CONTEXTS = ("test", "contract / tier-1")

# The rules the default branch has to be under. `pull_request` is what makes
# a direct push impossible, and the other two are what stop the branch being
# rewritten or removed instead.
BRANCH_RULES = ("pull_request", "deletion", "non_fast_forward")

# The rules a release tag has to be under, and the first one is the one that
# does the work. Measured on 2026-09-18 against a throwaway repository: with
# `deletion` and `non_fast_forward` alone, a `v*` tag can still be moved
# *forward* onto any descendant commit - including a hostile commit built on
# top of the one that was reviewed. Deletion protection is not immutability.
TAG_RULES = ("update", "deletion", "non_fast_forward")

# The ref pattern the platform's tag ruleset targets, and the branch
# condition it targets. `~DEFAULT_BRANCH` rather than a literal so that
# renaming the branch cannot leave the protection pointing at nothing.
TAG_PATTERN = "refs/tags/v*"
DEFAULT_BRANCH_CONDITION = "~DEFAULT_BRANCH"
EVERYTHING = "~ALL"


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": self.message}


@dataclass(frozen=True)
class Platform:
    """What the Projects are judged against: the contract, as it is today."""

    contract_version: str
    contract_document: str
    required_contexts: tuple = REQUIRED_CONTEXTS


RULES: list = []


def rule(rule_id: str, summary: str, needs=None):
    """Register a rule. `summary` is what the report prints beside its verdict.

    `needs` marks a rule that cannot be answered from these settings - today
    only the one that reads repository variables, which needs a token
    permission the audit would rather not hold than hold unnecessarily. Such
    a rule is reported as *not checked*, which is deliberately not the same
    as passing: an audit that reports an unanswered question in the Project's
    favour is worse than one that does not ask it.
    """

    def register(fn):
        fn.rule_id = rule_id
        fn.summary = summary
        fn.needs = needs
        RULES.append(fn)
        return fn

    return register


def _covers(ruleset, *patterns) -> bool:
    """Does this ruleset's condition cover the refs the platform cares about?

    Deliberately literal. Working out whether an arbitrary pattern covers
    every ref the platform means would be a second implementation of
    GitHub's matcher, and a matcher that is subtly wrong here reports a
    Project as protected because of a rule that does not reach it. An
    unrecognised pattern is reported, not interpreted.
    """
    include = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
    return any(pattern in include for pattern in patterns)


def _active(settings, target: str, *patterns) -> list:
    return [
        rs
        for rs in settings.rulesets
        if rs.get("target") == target
        and rs.get("enforcement") == "active"
        and _covers(rs, *patterns)
    ]


def _enforced(rulesets) -> set:
    return {r["type"] for rs in rulesets for r in rs.get("rules", [])}


def _ruleset_integrity(rule_id: str, rulesets, what: str):
    """Findings that apply to any ruleset the platform relies on.

    A bypass list is the shape drift takes in practice. Nobody removes a
    ruleset; somebody adds themselves to its bypass list to get one thing
    done, and the ruleset stays green on the settings page ever after. The
    runbook's line is that an exception added once is the same as not having
    the rule, so this reads it that way.
    """
    for rs in rulesets:
        name = rs.get("name", "?")
        actors = rs.get("bypass_actors") or []
        if actors:
            who = ", ".join(
                str(a.get("actor_type", a.get("actor_id", "?"))) for a in actors
            )
            yield Finding(
                rule_id,
                f"ruleset `{name}` has a bypass list ({who}). {what} that "
                "somebody can bypass is protection until the first time it "
                "is inconvenient: empty the list, including for the owner.",
            )
        excluded = (
            rs.get("conditions", {}).get("ref_name", {}).get("exclude") or []
        )
        if excluded:
            yield Finding(
                rule_id,
                f"ruleset `{name}` excludes {', '.join(excluded)} from its "
                "own condition, so those refs are governed by nothing it says.",
            )


def _inactive(settings, target: str, *patterns) -> list:
    return [
        rs
        for rs in settings.rulesets
        if rs.get("target") == target
        and rs.get("enforcement") != "active"
        and _covers(rs, *patterns)
    ]


@rule("protect-main", "the default branch cannot be pushed to, rewritten or deleted")
def protect_main(settings, platform):
    """The one control that still holds if an agent token leaks.

    Everything else in the platform - Doppler, the ephemeral GITHUB_TOKEN,
    the deploy key - protects secrets. This protects history, which is what
    a leaked `contents: write` token would otherwise rewrite at will. It is
    also what makes ADR-0008's "main is always releasable" enforced rather
    than merely reported.
    """
    covering = _active(settings, "branch", DEFAULT_BRANCH_CONDITION, EVERYTHING)
    literal = _active(settings, "branch", f"refs/heads/{settings.default_branch}")
    if not covering and not literal:
        for rs in _inactive(settings, "branch", DEFAULT_BRANCH_CONDITION, EVERYTHING):
            yield Finding(
                "protect-main",
                f"ruleset `{rs.get('name', '?')}` targets the default branch "
                f"but its enforcement is `{rs.get('enforcement')}`, so it "
                "judges nothing.",
            )
        yield Finding(
            "protect-main",
            "no active branch ruleset covers the default branch. This is the "
            "only control in the chain that survives a leaked token: without "
            "it, anything with `contents: write` can push straight to "
            f"{settings.default_branch}.",
        )
        return

    for finding in _ruleset_integrity("protect-main", covering + literal, "A branch"):
        yield finding

    if literal and not covering:
        yield Finding(
            "protect-main",
            f"the default branch is covered by the literal "
            f"`refs/heads/{settings.default_branch}` rather than by "
            "`~DEFAULT_BRANCH`. It is protected today and would stop being "
            "protected the moment the branch is renamed, silently.",
        )

    enforced = _enforced(covering + literal)
    for name in BRANCH_RULES:
        if name not in enforced:
            yield Finding(
                "protect-main",
                f"no ruleset covering the default branch carries a `{name}` "
                "rule.",
            )


@rule("required-checks", "a pull request cannot land until the platform's checks pass")
def required_checks(settings, platform):
    """Reporting a failure and refusing a merge are different things.

    Without this rule the suite and the contract check run, go red, and the
    merge button stays green. `strict` is the second half: without it a
    branch can land on a trunk it was never tested against, which makes the
    trunk the thing that discovers the problem.
    """
    rulesets = _active(
        settings,
        "branch",
        DEFAULT_BRANCH_CONDITION,
        EVERYTHING,
        f"refs/heads/{settings.default_branch}",
    )
    checks = [
        r
        for rs in rulesets
        for r in rs.get("rules", [])
        if r["type"] == "required_status_checks"
    ]
    if not checks:
        yield Finding(
            "required-checks",
            "no `required_status_checks` rule covers the default branch, so "
            "the suite and the contract check report and do not enforce.",
        )
        return

    contexts = {
        c["context"]
        for r in checks
        for c in r.get("parameters", {}).get("required_status_checks", [])
    }
    for wanted in platform.required_contexts:
        if wanted not in contexts:
            yield Finding(
                "required-checks",
                f"`{wanted}` is not a required status check. Present: "
                + (", ".join(f"`{c}`" for c in sorted(contexts)) or "nothing")
                + ".",
            )
    if not any(
        r.get("parameters", {}).get("strict_required_status_checks_policy")
        for r in checks
    ):
        yield Finding(
            "required-checks",
            "the required checks are not `strict`, so a branch can land on a "
            "trunk it was never tested against.",
        )


@rule("protect-releases", "an existing release tag cannot be moved or deleted")
def protect_releases(settings, platform):
    """A branch ruleset does not cover tags, and a tag is what deploys.

    Without this, a leaked `contents: write` token needs nothing else: move
    `v0.1.6` onto a commit of its choosing and the next deploy of that
    release ships it, with `main` untouched throughout.

    Creation is deliberately left open - tagging a release is the operator's
    deliberate act (ADR-0008) - so this closes moving and deleting, and the
    deploy workflow closes the rest by refusing a tag whose commit is not an
    ancestor of the default branch.
    """
    covering = _active(settings, "tag", TAG_PATTERN, EVERYTHING)
    if not covering:
        for rs in _inactive(settings, "tag", TAG_PATTERN, EVERYTHING):
            yield Finding(
                "protect-releases",
                f"ruleset `{rs.get('name', '?')}` targets `{TAG_PATTERN}` but "
                f"its enforcement is `{rs.get('enforcement')}`, so it judges "
                "nothing.",
            )
        patterns = sorted(
            p
            for rs in settings.rulesets
            if rs.get("target") == "tag"
            for p in rs.get("conditions", {}).get("ref_name", {}).get("include", [])
        )
        yield Finding(
            "protect-releases",
            f"no active tag ruleset covers `{TAG_PATTERN}`"
            + (f" (tag rulesets here cover {', '.join(patterns)})" if patterns else "")
            + ". A tag is what deploys, so this is an unguarded path to "
            "production that never touches the default branch.",
        )
        return

    for finding in _ruleset_integrity("protect-releases", covering, "A tag"):
        yield finding

    enforced = _enforced(covering)
    for name in TAG_RULES:
        if name not in enforced:
            yield Finding(
                "protect-releases",
                f"no active tag ruleset covering `{TAG_PATTERN}` carries an "
                f"`{name}` rule."
                + (
                    " Measured: without it, `deletion` and `non_fast_forward` "
                    "still let a release tag be moved forward onto any "
                    "descendant commit, so the tags are not immutable."
                    if name == "update"
                    else ""
                ),
            )


@rule("contract-version", "the Project pins the contract version that is current")
def contract_version(settings, platform):
    """The rule that notices a Project left behind by a version bump.

    A Project pins a major deliberately and nothing moves it, which is the
    point - nothing starts failing on its own. The cost of that is that a
    Project can sit on an old major indefinitely without anybody noticing,
    and this is the thing that notices.
    """
    if settings.manifest is None:
        yield Finding(
            "contract-version",
            f"{settings.manifest_error}, so this Project states no contract "
            "version. Tier 1 refuses this too, on every pull request.",
        )
        return
    pinned = settings.manifest.get("contractVersion")
    if pinned != platform.contract_version:
        yield Finding(
            "contract-version",
            f"pins `{pinned}` and the current contract is "
            f"`{platform.contract_version}`. Moving is deliberate: change the "
            "`contractVersion` in platform.json and the tag the workflow "
            "calls, together.",
        )


@rule("contract-document", "the Project's copy of CONTRACT.md is the canonical one")
def contract_document(settings, platform):
    """The comparison the checker deliberately does not make.

    A Project carries CONTRACT.md so the rules are legible without network
    access (ADR-0010), which is worth nothing if the copy has drifted from
    what is enforced - and worse than nothing, because it reads as
    authoritative. The checker compares the pinned *version* and not the
    text, so that a prose fix does not fail every Project at once. Here it
    is only a report, so it can afford to be exact.
    """
    if settings.contract_document is None:
        yield Finding(
            "contract-document",
            "CONTRACT.md is missing, so the rules are not readable from "
            "inside this Project. Tier 1 refuses this too.",
        )
        return
    theirs = _digest(settings.contract_document)
    ours = _digest(platform.contract_document)
    if theirs != ours:
        yield Finding(
            "contract-document",
            f"CONTRACT.md differs from the canonical copy ({theirs[:12]} "
            f"against {ours[:12]}). Nothing is failing because of it - the "
            "rules being enforced are the ones in this audit - but the "
            "document somebody reads in that repository is not them.",
        )


@rule(
    "no-repository-variables",
    "configuration is the committed Manifest, not a repository setting",
    needs=lambda settings: settings.variables is not None,
)
def no_repository_variables(settings, platform):
    """ADR-0010, and it is not tidiness.

    A repository variable is invisible to an agent whose scope is the
    repository: nothing in the files says the value exists, so a Project
    whose bundle is compiled against a hostname its own files never mention
    is opaque in exactly the way this platform rules out. `appHost` was such
    a variable until it became a field in the Manifest.
    """
    if settings.variables:
        yield Finding(
            "no-repository-variables",
            "declares repository variables ("
            + ", ".join(f"`{v}`" for v in settings.variables)
            + "). Configuration belongs in the committed Manifest, where an "
            "agent scoped to this repository can read it.",
        )


def _digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()
