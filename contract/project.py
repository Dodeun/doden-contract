"""The working tree under judgement.

Everything a rule needs to read is gathered here once, so that a rule is an
assertion rather than a file-reading exercise, and so that a tree which
cannot be read at all produces violations rather than a stack trace.
"""

import json
import re
import subprocess
from pathlib import Path

from . import compose
from . import schema as jsonschema

MANIFEST = "platform.json"
SCHEMA = "platform.schema.json"
CONTRACT_DOC = "CONTRACT.md"

# The one machine-readable line in an otherwise prose document.
PINNED_VERSION = re.compile(r"^Contract version:\s*`?(v[0-9]+)`?\s*$", re.M)


class Project:
    def __init__(self, tree, contract_root, version):
        self.tree = Path(tree)
        self.contract_root = Path(contract_root)
        self.version = version
        self.notes = []

        self.manifest = None
        self.manifest_problems = []
        self._read_manifest()

        self.contract_doc = None
        self.contract_doc_version = None
        self._read_contract_doc()

        self._files = None
        self.tracked_by_git = None

        self.compose_source = None
        self.rendered = None
        self.render_error = None
        self._render()

    # -- the production Stack ---------------------------------------------

    def _render(self):
        path = self.tree / compose.COMPOSE_FILE
        if path.is_file():
            self.compose_source = path.read_text(encoding="utf-8")
        self.rendered, self.render_error = compose.render(self.tree)

    @property
    def services(self):
        return compose.services(self.rendered)

    @property
    def variable_uses(self):
        if self.compose_source is None:
            return []
        return compose.variable_uses(self.compose_source)

    # -- what the repository contains -------------------------------------

    @property
    def files(self):
        """Repository-relative paths, tracked by git where git can say.

        "Committed" is a question about the index, not about the directory:
        a developer's own .env is correctly ignored and must never fail
        anybody's check. Where there is no repository - a fixture, an export -
        the filesystem is all there is, and the checker says so rather than
        reporting that it found nothing.
        """
        if self._files is None:
            self._files = self._list_files()
        return self._files

    def _list_files(self):
        try:
            proc = subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=str(self.tree),
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            proc = None
        if proc is not None and proc.returncode == 0:
            self.tracked_by_git = True
            return [name for name in proc.stdout.split("\0") if name]

        self.tracked_by_git = False
        self.note(
            "this tree is not a git repository, so the checker read the "
            "filesystem instead of the index. An ignored file is "
            "indistinguishable from a committed one here."
        )
        skip = {".git", "node_modules", "dist", "build", ".venv", "__pycache__"}
        out = []
        for path in self.tree.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.tree)
            if skip & set(relative.parts):
                continue
            out.append(relative.as_posix())
        return sorted(out)

    # -- the contract this Project carries a copy of ----------------------

    @property
    def contract_major(self):
        """The major of the checker running - what a Project pins against."""
        return self.version.split(".", 1)[0]

    def _read_contract_doc(self):
        path = self.tree / CONTRACT_DOC
        if not path.is_file():
            return
        self.contract_doc = path.read_text(encoding="utf-8")
        found = PINNED_VERSION.search(self.contract_doc)
        if found:
            self.contract_doc_version = found.group(1)

    # -- the Manifest ----------------------------------------------------

    def _read_manifest(self):
        path = self.tree / MANIFEST
        if not path.is_file():
            self.manifest_problems = [
                MANIFEST + " is missing. A Project states its own identity in "
                "a committed " + MANIFEST + " - slug, appName, appHost, "
                "profile, addons, contractVersion - so that an agent which can "
                "only see this repository can still say what this Project is "
                "and where it deploys."
            ]
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.manifest_problems = [
                MANIFEST + " is not valid JSON: " + str(exc)
            ]
            return
        if not isinstance(data, dict):
            self.manifest_problems = [MANIFEST + " must contain a JSON object."]
            return

        older = self._older_shape(data)
        if older:
            self.manifest_problems = older
            return

        definition = json.loads(
            (self.contract_root / SCHEMA).read_text(encoding="utf-8")
        )
        problems = jsonschema.validate(definition, data)
        if problems:
            self.manifest_problems = [
                MANIFEST + " - " + problem for problem in problems
            ]
            return
        self.manifest = data

    def _older_shape(self, data):
        """A Manifest written for an earlier contract, told so in those terms.

        The schema's own answer to a `v1` Manifest is that "addons" must be an
        object and not an array, which is true and is not what anybody needs
        to read. The person who meets this message has not opened the ticket
        that changed the shape and may not know a version changed at all - so
        it says which version this Manifest is, which one is running, and what
        the new shape looks like, and it says it *instead of* the schema's
        complaints rather than beside them, because the ones the shape
        causes drown the message explaining it: a `v1` Manifest also has no
        seed command under the new conditional, and reporting that would send
        somebody to add a field they already have.

        The cost is that a `v1` Manifest with an *unrelated* fault - a missing
        slug, a typo'd host - is told about the shape first and about the
        fault on the next run. Two rounds rather than one, and the right trade
        here: nothing else about this Manifest can be judged until its shape
        is the shape being judged.
        """
        addons = data.get("addons")
        if isinstance(addons, list):
            return [
                MANIFEST + ' declares "addons" as an array, which is the shape '
                "contract v1 used. The checker running is "
                + self.contract_major + ", where addons is an object whose "
                "keys are the Add-ons and whose values carry that Add-on's "
                'configuration: "addons": { "database": { "provider": '
                '"postgresql" } }. A Project with no Add-ons writes {}. '
                "There is no `oauth` Add-on any more and nothing replaced it - "
                "a login is the Project's own code (ADR-0013) - so it comes "
                "out of the Manifest and stays in the application. Move this "
                "Project by changing the Manifest and the tag its workflow "
                "calls together, or keep it on v1 by pinning the workflow at "
                "@v1, which no longer moves."
            ]
        if isinstance(addons, dict) and "oauth" in addons:
            return [
                MANIFEST + ' declares an "oauth" Add-on. There is not one. An '
                "Add-on is an optional dependency on a Shared Platform Service, "
                "and this platform runs no identity provider (ADR-0009): a "
                "login is the Project's own code however much of it there is "
                "(ADR-0013). Remove the key; nothing else about the Project's "
                "login changes."
            ]
        return []

    # -- convenience for the rules ---------------------------------------

    @property
    def slug(self):
        return self.manifest.get("slug") if self.manifest else None

    @property
    def addons(self):
        """The declared Add-ons: a name mapped to that Add-on's configuration.

        `None` - not `{}` - when the Manifest could not be read at all. An
        Add-on rule must be able to tell "this Project declares none" from
        "nobody knows what this Project declares", because the `networks` and
        `database-url` rules refuse in *both* directions and would otherwise
        refuse a Project whose only fault is a Manifest the first rule has
        already reported.
        """
        return dict(self.manifest.get("addons", {})) if self.manifest else None

    def has_addon(self, name):
        addons = self.addons
        return addons is not None and name in addons

    def addon(self, name):
        """One Add-on's configuration, or `None` when it is not declared."""
        addons = self.addons
        return (addons or {}).get(name) if addons is not None else None

    def note(self, text):
        self.notes.append(text)
