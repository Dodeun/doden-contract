"""Reading a Project's settings off GitHub. The only networked code here.

The seam this file exists to create: everything past it takes a `Settings`
record and returns findings, so every rule can be tested against recorded
settings rather than against a live repository. A rule that can only be
exercised by breaking a real Project's configuration is a rule nobody will
exercise twice.

Standard library only, like the checker, and for the same reason: this runs
on a schedule that nobody watches, and a dependency is a thing that can break
it on a morning when nobody touched it.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import AuditError

API = "https://api.github.com"
API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class Settings:
    """Everything the audit knows about one Project, fetched once.

    `variables` is `None` rather than `[]` when the token could not read
    them. The two mean opposite things and the rule that reads it says so:
    an empty list is a Project with no repository variables, and `None` is a
    question nobody answered.
    """

    repository: str
    default_branch: str
    rulesets: list = field(default_factory=list)
    manifest: dict | None = None
    manifest_error: str | None = None
    contract_document: str | None = None
    variables: list | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        """Build from a recorded payload. This is what the fixtures are."""
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise AuditError(
                "recorded settings carry fields this audit does not know: "
                + ", ".join(sorted(unknown))
            )
        return cls(**raw)


def _get(path: str, token: str, raw: bool = False):
    """One GET. Returns the decoded body, or None on 404."""
    request = urllib.request.Request(API + path)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("X-GitHub-Api-Version", API_VERSION)
    request.add_header("User-Agent", "doden-contract-audit")
    request.add_header(
        "Accept",
        "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        # 401 and 403 are about the token, not about the Project. They are
        # raised rather than returned so that no rule can mistake them for a
        # setting that is absent.
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise AuditError(
            f"GET {path} answered {exc.code}: {detail.strip()}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AuditError(f"GET {path} did not complete: {exc.reason}") from exc
    return body if raw else json.loads(body)


def fetch(repository: str, token: str) -> Settings:
    """Read one Project's settings.

    A Project the token cannot see is an error and not a verdict: `None` here
    would flow into the rules as "no rulesets", and an audit that reports a
    repository it cannot read as unprotected is an audit that cries wolf on
    the day somebody renames a repository.
    """
    repo = _get(f"/repos/{repository}", token)
    if repo is None:
        raise AuditError(
            f"{repository} answered 404. Either it does not exist under that "
            "name, or this token cannot see it - and the audit cannot tell "
            "those apart, so it refuses to call it either conforming or "
            "drifted."
        )

    # The list endpoint carries no rules, only names and ids, so each ruleset
    # is read again by id. That is the whole reason this is two calls per
    # ruleset rather than one: a ruleset's *rules* are what the audit judges,
    # and a listing that shows a ruleset called `protect-main` says nothing
    # about what it enforces.
    rulesets = []
    for summary in _get(f"/repos/{repository}/rulesets", token) or []:
        full = _get(f"/repos/{repository}/rulesets/{summary['id']}", token)
        if full is not None:
            rulesets.append(full)

    manifest = None
    manifest_error = None
    body = _get(f"/repos/{repository}/contents/platform.json", token, raw=True)
    if body is None:
        manifest_error = "platform.json is missing"
    else:
        try:
            manifest = json.loads(body)
        except json.JSONDecodeError as exc:
            manifest_error = f"platform.json is not valid JSON: {exc}"

    document = _get(f"/repos/{repository}/contents/CONTRACT.md", token, raw=True)

    # Variables need their own token permission, and the audit is meant to
    # hold the fewest it can. A token without it reports the question as
    # unanswered rather than answered in the Project's favour.
    try:
        payload = _get(f"/repos/{repository}/actions/variables?per_page=100", token)
        variables = sorted(v["name"] for v in (payload or {}).get("variables", []))
    except AuditError:
        variables = None

    return Settings(
        repository=repository,
        default_branch=repo["default_branch"],
        rulesets=rulesets,
        manifest=manifest,
        manifest_error=manifest_error,
        contract_document=document,
        variables=variables,
    )
