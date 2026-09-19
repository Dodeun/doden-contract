# doden-contract

Tier 1 of the Platform Contract: the rules a Project's repository must
satisfy to be deployable by the `doden.dev` platform, the schema for the file
a Project states its identity in, and the checker that enforces both.

**[`CONTRACT.md`](./CONTRACT.md) is the document.** It has the rules in
prose, each with the reason it exists. This file is about the repository.

## Why this is public

The checker holds no secret and asks for none. Publishing it removes a
repository setting from every Project — a private reusable workflow needs the
reusable-workflow access setting turned on per caller, and a private image
needs a registry credential — and it lets an agent whose scope is a single
Project *read* what will judge it, which a private platform repository would
not.

It is **not** a Template. Nothing is copied out of here except `CONTRACT.md`;
Projects call the workflow. The Template is a separate repository.

## Using it

From a Project's CI:

```yaml
jobs:
  contract:
    uses: Dodeun/doden-contract/.github/workflows/contract-check.yml@v1
```

From a working tree:

```sh
git clone --depth 1 -b v1 https://github.com/Dodeun/doden-contract ~/.doden-contract
python3 ~/.doden-contract/check.py .
```

On Windows the interpreter is usually `python` rather than `python3`; CI and
the workflow use `python3`, which is what Linux and macOS have.

`--json` emits the verdict as JSON. Exit status is **0** when the tree
conforms, **1** when it does not, and **2** when the checker could not run at
all — a missing `docker compose`, a schema keyword the validator does not
implement. Exit 2 is deliberately not exit 1: neither of those is a statement
about the Project, and conflating them sends somebody to edit a Compose file
to fix their laptop.

## What is in here

| | |
| --- | --- |
| `CONTRACT.md` | The rules in prose, both tiers. Copied into every Project, unedited. |
| `platform.schema.json` | The Manifest's schema. A Project points `$schema` at it for editor support; the checker reads it from its own checkout. |
| `check.py` | The tier-1 entry point. |
| `contract/rules.py` | The tier-1 rules. One function each, with the reason in its docstring. |
| `contract/compose.py` | Rendering the Stack, and reading the source for the rules a render erases. |
| `contract/schema.py` | A JSON Schema validator covering exactly what the schema uses. |
| `contract/project.py` | The tree under judgement, read once. |
| `fixtures/` | A conforming Project, and a failing example for every tier-1 rule. |
| `audit.py` | The tier-2 entry point. |
| `audit/rules.py` | The tier-2 rules — repository settings — same shape, one function each. |
| `audit/settings.py` | The only networked code in here: one Project's settings, read off GitHub. |
| `audit/report.py` | The report, and the Discord message. |
| `audit/fixtures/` | Recorded settings, and an overlay for every tier-2 rule. |
| `tests/` | The suite that runs both. |

`audit.py` and the `audit/` package share a name, and Python resolves that
towards the package: `import audit` is `audit/__init__.py`, and the entry
point is reached by running it. Tier 1's pair sidesteps the question by
being called `check.py` and `contract/`.

## The dependencies, and why there are none

Python 3 and `docker compose`. Nothing from PyPI, npm or a container
registry.

This is pinned by a tag and nothing updates it. A dependency here is
something that can break a Project's CI on a morning when nobody touched
either the Project or the contract. The cost is `contract/schema.py`, a
JSON Schema validator written by hand — which is a real cost, and is why it
**refuses to run** against a schema using a keyword it does not implement,
rather than ignoring it. A keyword that is silently ignored is a rule that
does not exist.

`docker compose` is not a dependency the contract could avoid: the rules are
asserted against the *rendered* Compose file, because grepping the source
passes a Stack whose `ports:` arrives through a variable.

## The fixtures

`fixtures/base` is a conforming Project with the database Add-on. Everything
else is an **overlay**: only the files that differ, merged over the base by
the test harness, so the fixture *is* the diff that makes a rule fire. A
`.remove` file in an overlay lists paths to delete, which is how "this file
is missing" is expressed.

`fixtures/pass/` are trees that must come back clean. `fixtures/fail/` are
trees that must fire exactly one rule, with a message naming what to fix.

The harness copies this repository's own `CONTRACT.md` into each merged tree
— exactly as the platform copies it into a Project — so a fixture cannot
drift from the document it is supposed to satisfy. It also `git init`s the
tree, because "no committed `.env`" is a question about the index and not
about the directory.

```sh
python3 -m unittest discover -s tests
```

## Releasing

Versions are pinned by a **moving major tag**. A Project pins `@v1` and picks
up fixes without doing anything.

1. Update `VERSION`.
2. Set `ref:` in `.github/workflows/contract-check.yml` to the same exact
   version. This is the step that is easy to forget and expensive to get
   wrong: the workflow pins the checker it runs, so a Project calling
   `@v1.0.1` must get the `v1.0.1` checker and not whatever `v1` points at
   today. A Project running rules its own `CONTRACT.md` does not describe is
   the disagreement the `contract-version` rule exists to catch, arriving
   from the one direction that rule cannot see.
3. If a rule changed, say so in `CONTRACT.md` — that copy is what every
   Project reads, and a rule the prose does not mention is a rule that will
   surprise somebody.
4. Commit, tag the exact version, then move the major onto the same commit:

```sh
git tag v1.0.1
git tag -f v1
git push origin v1.0.1
git push -f origin v1
```

Because the major tag lands on the commit whose workflow names the matching
exact version, `@v1` and `@v1.0.1` run the same bytes.

A change that would newly refuse a Project which passes today is a **major**
bump. Projects move to a new major deliberately, one at a time, by changing
the `@v1` in their workflow and the `contractVersion` in their Manifest —
nothing starts failing on its own.

Changing `CONTRACT.md` puts every Project's copy out of date until each one
is updated. Nothing fails because of it — the tier-1 rule compares the pinned
*version*, not the text, so a prose fix cannot refuse every Project at once —
but the tier-2 audit reports the drift by name, which is the point of having
it. Update the copies in the same breath, or expect the audit to say so.

## Tier 2, and why the token is not here

Tier 2 is the repository's own settings: the rulesets, the required checks,
the empty bypass list. None of it is a file, so none of it travels in a copy
of a repository, and reading it needs a credential.

`audit.py` is that judgement, and it holds no credential and no list of
Projects:

```sh
# against recorded settings - no token, no network
python3 audit.py --settings audit/fixtures/conforming.json

# against real repositories
AUDIT_TOKEN=... python3 audit.py --projects projects.json
```

**The schedule, the list of Projects and the token live in the private
platform repository**, which is the deliberate part. This repository is
public because it holds no secret and asks for none — that is what removes a
per-Project access grant, and it is a claim printed at the top of this file.
Putting the platform's most dangerous credential here, in the one repository
whose defining property is that it has none, would make that claim false.

`AUDIT_TOKEN` may read repository settings and may write nothing. Exit status
is the checker's: **0** every Project conforms, **1** something drifted, **2**
the audit could not run — no token, a Project that answers 404. A Project
that could not be read is never reported as conforming, because an audit that
cannot see is otherwise indistinguishable from an audit that sees nothing
wrong.
