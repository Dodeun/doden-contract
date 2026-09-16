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

    # -- convenience for the rules ---------------------------------------

    @property
    def slug(self):
        return self.manifest.get("slug") if self.manifest else None

    @property
    def addons(self):
        return list(self.manifest.get("addons", [])) if self.manifest else None

    def has_addon(self, name):
        addons = self.addons
        return addons is not None and name in addons

    def note(self, text):
        self.notes.append(text)
