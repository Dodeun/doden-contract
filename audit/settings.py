"""Reading a Project's settings, and the Prototypes' App, off GitHub. The only
networked code here.

The seam this file exists to create: everything past it takes a `Settings`
or an `AppSettings` record and returns findings, so every rule can be tested
against recorded settings rather than against a live repository. A rule that
can only be exercised by breaking a real Project's configuration is a rule
nobody will exercise twice.

Standard library only, like the checker, and for the same reason: this runs
on a schedule that nobody watches, and a dependency is a thing that can break
it on a morning when nobody touched it. The App's JWT is signed by the
`openssl` binary, which every runner and every machine here already has,
rather than by a Python package that would have to be installed.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import AuditError, NotPermitted

API = "https://api.github.com"
API_VERSION = "2022-11-28"

# Where an Add-on's document lives in a Project, which is where it lives in
# this repository. One path, so that a copy and its original are found the
# same way.
ADDON_DOCUMENTS = "docs/addons"

# How many of them this will read per Project. See `fetch`.
MAX_ADDON_DOCUMENTS = 25


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
    addon_documents: dict = field(default_factory=dict)
    variables: list | None = None
    # What GitHub says it *applies* to the default branch, as opposed to what
    # the rulesets declare. `None` is a question nobody answered, the same as
    # `variables`; `[]` is GitHub applying nothing at all.
    applied_branch_rules: list | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        """Build from a recorded payload. This is what the fixtures are.

        Both directions are refused, and for the same reason the schema
        validator refuses a keyword it does not implement: a field silently
        ignored is a rule that does not exist, and a field silently missing
        is a rule judging a default nobody chose.
        """
        return _from_record(cls, raw, {"repository", "default_branch"}, "recorded settings")


@dataclass(frozen=True)
class AppSettings:
    """What the audit knows about the Prototypes' GitHub App (ADR-0012).

    Not a repository: the App's own settings and its one installation's.
    `registered_permissions` are what the App's settings ask for, and
    `granted_permissions` what the installation has accepted - which is what
    a token minted from it actually carries. They differ for as long as a
    change to the App waits for the installation to accept it.
    """

    slug: str
    registered_permissions: dict
    granted_permissions: dict
    repository_selection: str
    repositories: list

    @classmethod
    def from_dict(cls, raw: dict) -> "AppSettings":
        """Build from a recorded payload, refusing the same two ways."""
        return _from_record(cls, raw, set(cls.__dataclass_fields__), "recorded App settings")


def _from_record(cls, raw, required: set, what: str):
    """A record of `cls` from a recorded payload. See `Settings.from_dict`."""
    if not isinstance(raw, dict):
        raise AuditError(f"{what} should be a JSON object")
    unknown = set(raw) - set(cls.__dataclass_fields__)
    if unknown:
        raise AuditError(
            f"{what} carry fields this audit does not know: " + ", ".join(sorted(unknown))
        )
    missing = required - set(raw)
    if missing:
        raise AuditError(f"{what} are missing: " + ", ".join(sorted(missing)))
    return cls(**raw)


def _get(path: str, token: str, raw: bool = False):
    """One GET. Returns the decoded body, or None on 404."""
    return _request("GET", path, token, raw=raw)


def _request(method: str, path: str, token: str, body=None, raw: bool = False):
    """One request. Returns the decoded body, or None on 404 or on no body."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("X-GitHub-Api-Version", API_VERSION)
    request.add_header("User-Agent", "doden-contract-audit")
    request.add_header(
        "Accept",
        "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", "replace")[:200]
        # 403 is "this token may not read that", which two callers - the
        # variables and the applied rules - are allowed to treat as an
        # unanswered question. Everything else - an expired token, a rate
        # limit, a bad gateway - is the audit being unable to run, and is
        # raised as such so that no rule can mistake it for a setting that is
        # absent.
        error = NotPermitted if exc.code == 403 else AuditError
        raise error(f"{method} {path} answered {exc.code}: {detail.strip()}") from exc
    except urllib.error.URLError as exc:
        raise AuditError(f"{method} {path} did not complete: {exc.reason}") from exc
    if raw:
        return text
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # An HTML error page from a proxy, or a truncated response. Neither
        # is a statement about the Project, and letting it escape as a
        # traceback would exit 1 - the status that means "drifted".
        raise AuditError(f"{method} {path} did not answer JSON: {exc}") from exc


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
    default_branch = repo.get("default_branch")
    if not default_branch:
        raise AuditError(f"{repository} names no default branch")

    rulesets = []
    for summary in _get(f"/repos/{repository}/rulesets", token) or []:
        full = _get(f"/repos/{repository}/rulesets/{summary['id']}", token)
        if full is not None:
            rulesets.append(full)

    # The rules GitHub applies to the default branch, from every active
    # ruleset whatever level it was configured at, and from none in
    # `evaluate` or `disabled`. A ruleset can say `active` and produce
    # nothing here - which is what a private repository's rulesets are
    # expected to do when the account's GitHub Pro lapses (decision 31 of
    # phase 4). There is no such endpoint for a tag: read 2026-10-04 in the
    # REST reference, and `rules/tags/...` answers 404.
    #
    # **Any refusal here is the audit failing, never a question left open.**
    # Unlike the variables, this endpoint needs only Metadata: read, which
    # every fine-grained token carries, so a 403 or a 404 is not a missing
    # permission - and it may be exactly how a lapsed plan answers. Reported
    # as *not checked*, it would leave the run green on the day this rule
    # exists for. Raised, it is exit 2, which Discord hears about.
    #
    # One page of 100, like the variables: the platform's rulesets apply four
    # rules, and the endpoint's default page is 30.
    path = f"/repos/{repository}/rules/branches/{default_branch}?per_page=100"
    try:
        applied = _get(path, token)
    except NotPermitted as exc:
        raise AuditError(
            f"{repository}: GitHub refused to say which rules apply to "
            f"{default_branch} ({exc}). The token needs nothing beyond "
            "Metadata: read for this, so look at the account's plan as well "
            "as the token."
        ) from exc
    if not isinstance(applied, list):
        raise AuditError(
            f"{repository}: GET {path} answered "
            + ("404" if applied is None else "something other than a list")
            + ", so which rules GitHub applies is unknown. It is not reported "
            "as conforming."
        )
    applied_branch_rules = applied

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

    # Listed rather than asked for by name, so that a document for an Add-on
    # the Project does not declare is *visible*. Asking only for the ones it
    # declares would make the two directions unaskable: a Project carrying
    # docs/addons/database.md with no database is describing a capability it
    # has not got, which is precisely what `oauth` was.
    addon_documents = {}
    listed = [
        entry.get("name", "")
        for entry in _get(f"/repos/{repository}/contents/{ADDON_DOCUMENTS}", token) or []
        if entry.get("type") == "file" and entry.get("name", "").endswith(".md")
    ]
    # Bounded, because this is a directory in somebody else's repository and
    # this audit runs unattended on a schedule: without a ceiling, a Project
    # that put four hundred files there would make four hundred requests
    # every Monday. Sorted so the ceiling cuts the same documents each week
    # rather than a different arbitrary set. There is one Add-on.
    for name in sorted(listed)[:MAX_ADDON_DOCUMENTS]:
        addon_documents[name[: -len(".md")]] = _get(
            f"/repos/{repository}/contents/{ADDON_DOCUMENTS}/{name}", token, raw=True
        )

    # Variables need their own token permission, and the audit is meant to
    # hold the fewest it can. A token without it reports the question as
    # unanswered rather than answered in the Project's favour - which is why
    # only `NotPermitted` is caught here and a 404 becomes `None` rather than
    # an empty list. An empty list is "this Project declares no variables",
    # and nothing that failed is allowed to say that.
    try:
        payload = _get(f"/repos/{repository}/actions/variables?per_page=100", token)
        variables = (
            None
            if payload is None
            else sorted(v["name"] for v in payload.get("variables", []))
        )
    except NotPermitted:
        variables = None

    return Settings(
        repository=repository,
        default_branch=default_branch,
        rulesets=rulesets,
        manifest=manifest,
        manifest_error=manifest_error,
        contract_document=document,
        addon_documents=addon_documents,
        variables=variables,
        applied_branch_rules=applied_branch_rules,
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _app_jwt(app_id, key: str) -> str:
    """A JWT for the App, valid nine minutes, signed with its private key.

    GitHub refuses one valid for more than ten, and `iat` is set a minute in
    the past against a clock that runs slightly ahead of GitHub's. The key
    reaches `openssl` through a file only this process can read, removed
    before this returns; it is never on a command line and never printed.
    """
    openssl = shutil.which("openssl")
    if not openssl:
        raise AuditError("openssl is not installed, so the App's JWT cannot be signed")
    now = int(time.time())
    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode()
    )
    signing_input = f"{head}.{claims}".encode()
    with tempfile.TemporaryDirectory(prefix="doden-app-") as directory:
        path = os.path.join(directory, "key.pem")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(key)
        signed = subprocess.run(
            [openssl, "dgst", "-sha256", "-sign", path],
            input=signing_input,
            capture_output=True,
        )
    if signed.returncode != 0 or not signed.stdout:
        raise AuditError(
            "the App's private key could not sign a JWT. APP_PRIVATE_KEY should "
            "hold the whole .pem file GitHub downloaded, header lines included."
        )
    return f"{head}.{claims}.{_b64url(signed.stdout)}"


def fetch_app(app: dict, key: str) -> AppSettings:
    """Read the Prototypes' App and the repositories its installation holds.

    No credential short of the App's own key can: measured 2026-10-04 by
    ticket 35's probe, a private App answers 404 to `GET /apps/{slug}` with
    no token, with a fine-grained token and with the operator's own, and
    `GET /user/installations/...` answers 403 to both tokens. So the key is
    held here - and spent on as little as it can be. The JWT reads the App
    and its installation, which is all a JWT can read. The listing needs an
    installation token, and the one minted here may read metadata and
    nothing else, so the token that touches repositories cannot change one.
    It is revoked as soon as the listing is done, whether or not it worked.
    """
    slug, installation = app["slug"], app["installationId"]
    jwt = _app_jwt(app["appId"], key)

    me = _request("GET", "/app", jwt)
    if not isinstance(me, dict) or me.get("slug") != slug:
        found = me.get("slug") if isinstance(me, dict) else None
        raise AuditError(
            f"the key in APP_PRIVATE_KEY belongs to {found}, not {slug}, so "
            f"{slug} was not read."
            if found
            else f"GET /app did not answer with an App, so {slug} was not read."
        )

    held = _request("GET", f"/app/installations/{installation}", jwt)
    if not isinstance(held, dict):
        raise AuditError(
            f"{slug}'s installation {installation} answered 404: the App is no "
            "longer installed there, or the id on the list is wrong. Which "
            "repositories it holds is unknown, and it is not reported as "
            "holding none."
        )

    minted = _request(
        "POST",
        f"/app/installations/{installation}/access_tokens",
        jwt,
        body={"permissions": {"metadata": "read"}},
    )
    if not isinstance(minted, dict) or not minted.get("token"):
        raise AuditError(f"{slug}'s installation {installation} minted no token.")
    token = minted["token"]
    try:
        repositories = []
        page = 1
        while True:
            listed = _request(
                "GET", f"/installation/repositories?per_page=100&page={page}", token
            )
            # Anything but the documented shape is the audit unable to read
            # the installation. A KeyError here would be a traceback, which
            # exits 1 - "drifted" - and a listing without its count would end
            # after one page, hiding whatever was on the next: a Project there
            # is the whole of what the rule looks for.
            batch = listed.get("repositories") if isinstance(listed, dict) else None
            total = listed.get("total_count") if isinstance(listed, dict) else None
            if not isinstance(batch, list) or not isinstance(total, int) or not all(
                isinstance(r, dict) and isinstance(r.get("full_name"), str) for r in batch
            ):
                raise AuditError(
                    f"{slug}'s installation did not list its repositories in the "
                    "shape GitHub documents, so which ones it holds is unknown."
                )
            repositories.extend(r["full_name"] for r in batch)
            if len(repositories) >= total:
                break
            if not batch:
                raise AuditError(
                    f"{slug}'s installation counts {total} repositories and "
                    f"listed {len(repositories)}."
                )
            page += 1
    finally:
        # The token expires within the hour anyway. A revocation that fails
        # is therefore not the audit failing, and must not hide a finding.
        try:
            _request("DELETE", "/installation/token", token)
        except AuditError:
            pass

    return AppSettings(
        slug=slug,
        registered_permissions=me.get("permissions") or {},
        granted_permissions=held.get("permissions") or {},
        repository_selection=held.get("repository_selection", ""),
        repositories=sorted(repositories),
    )
