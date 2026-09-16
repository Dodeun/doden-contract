# The Platform Contract

Contract version: `v1`

The rules a Project's repository must satisfy to be deployable by the
platform. This file is canonical in
[`Dodeun/doden-contract`](https://github.com/Dodeun/doden-contract) and is
copied, unedited, into every Project — so that an agent whose scope is one
repository can read what will judge it without reaching the network, and run
the same command CI runs.

Every rule below has a reason. A rule with no reason is a rule nobody can
argue with when it is wrong, and this file is meant to be argued with.

## What this is not

A green tier-1 check does **not** mean a Project is deployable. It means the
files in the repository are in order. Two things are deliberately outside it:

- **Tier 2 — the repository's own settings.** Branch and tag rulesets,
  required status checks, the empty bypass list. These cannot travel in a
  copy of a repository, so they are audited centrally rather than checked
  here, and no Project holds a credential that can read its own settings.
- **Host and account facts.** The directory a Stack deploys into, the scoping
  of a secrets token, the ownership of a database. These are outside the
  contract entirely, so that a green check is never mistaken for "this will
  deploy". That claim belongs to the deploy script's pre-flight.

## Running the check

It is one implementation. CI and a laptop run the same file, so they cannot
disagree.

**From a Project's CI** — no secret, and none can be asked for:

```yaml
jobs:
  contract:
    uses: Dodeun/doden-contract/.github/workflows/contract-check.yml@v1
```

**From a working tree**, by a human or by an agent:

```sh
git clone --depth 1 -b v1 https://github.com/Dodeun/doden-contract ~/.doden-contract   # once
python3 ~/.doden-contract/check.py .                                                   # per run
```

Python 3 and Docker Compose are all it needs. The checker has no third-party
dependency, on purpose: it is pinned by a tag and nothing updates it, so it
must not be able to break because of somebody else's release.

## The Manifest

`platform.json`, at the root, is the Project's identity — and the first rule,
because every other rule reads it. Its schema is
[`platform.schema.json`](https://github.com/Dodeun/doden-contract/blob/v1/platform.schema.json).

```json
{
  "$schema": "https://raw.githubusercontent.com/Dodeun/doden-contract/v1/platform.schema.json",
  "slug": "example-project",
  "appName": "Example Project",
  "appHost": "example.doden.dev",
  "profile": "node-web",
  "addons": ["database", "oauth"],
  "contractVersion": "v1",
  "seedCommand": "npm run seed --workspace=backend"
}
```

| Field | What it is |
| --- | --- |
| `slug` | The Project's name everywhere a machine reads it: the Compose project name, the Traefik router and service names, the directory on the host, the image repository. |
| `appName` | The Project's name where a person reads it. |
| `appHost` | The public hostname. Read at image-build time as well as at deploy time — a frontend bundle is compiled against it — so changing it means republishing, not redeploying. |
| `profile` | The Template this Project was copied from. A closed list: the contract names no language, and a Profile is the one place a language is allowed to appear, so adding one is a version bump rather than a free-text field. |
| `addons` | Optional capabilities declared rather than inherited — `database`, `oauth`. Shared files stay byte-identical across Projects and enable the behaviour conditionally, which is what keeps drift from the Template measurable. |
| `contractVersion` | The major version this Project is held to. Matches the tag its workflow pins and the version this file carries. |
| `seedCommand` | The command a Preview runs to fill an empty database with synthetic data. Required when `database` is declared. |

Configuration lives here rather than in repository variables because a
repository variable is invisible to an agent that can only see the
repository: a Project whose bundle is compiled against a hostname it never
mentions is opaque in exactly the way this platform rules out.

## The rules

### `manifest` — `platform.json` exists and validates

Every other rule reads it. A Project that cannot say what it is cannot be
judged, and an agent that opens the repository cannot say where it deploys.

### `contract-version` — `CONTRACT.md` pins the checker that is running

This file, the Manifest's `contractVersion`, and the checker must agree. A
copy that pins another version is prose that reads as authoritative and is
not being enforced, which is worse than having no copy at all.

### `compose-file` — the production Stack renders from the Manifest alone

The Stack is one file, `docker-compose.prod.yml`, at the root. It must render
with only the platform's own values set (see `identity-variables` below),
because that is all the platform knows when it deploys. A Stack requiring a
variable nobody supplies is a Stack that fails at the point of deploying
rather than at the point of checking.

Every rule after this one is asserted against the **rendered** file —
`docker compose config` with the platform's values — and not against the
source text. Grepping the source would pass a Stack whose `ports:` arrives
through a variable. Where a rule reads the source instead, it says so and
says why.

### `no-published-ports` — nothing binds a host port

Traefik owns 80 and 443, and routing is a platform concern. This rule exists
because the first Project shipped its own nginx on both, and deploying it
unchanged would have taken the whole VPS down. A Project that ships its own
reverse proxy is a contract violation, not a preference.

### `no-build` — nothing in the production Stack builds

The host pulls what CI published. Production holds images, not source: it
carries no clone, no `.git` and no deploy key, and it compiles nothing.

### `image-tag` — every image is named by an explicit, immutable tag

Every image's tag comes from `IMAGE_TAG` — the commit the images were
published under — or the image is pinned by digest. No literal tag, and no
missing tag.

A Rollback is re-pointing the Stack at a previous Release's published images,
so a tag that can be moved names different bytes tomorrow and a Release
pinned to one is not pinned to anything. Checked by asking where the tag came
from rather than what it says, because a denylist of floating names —
`latest`, `main`, `stable` — is the obvious implementation and the wrong one:
`v1` is on no such list, and this contract's own release process moves `v1`
every release.

### `identity-variables` — what the platform supplies fails rather than guesses

`PROJECT_SLUG`, `APP_HOST`, `IMAGE_TAG` and `IMAGE_REPO_PREFIX` come from the
Manifest and the release. None of them may carry a default, and
`PROJECT_SLUG` and `IMAGE_TAG` must use the `${VAR:?}` form that refuses to
render when unset.

This is the one rule the rendered file cannot answer — a default is invisible
once it has been substituted — so it reads the source. It is also the rule
with the worst failure mode. A Project copied from another, whose
`PROJECT_SLUG` nobody set, starts a Compose project under the original's name
and registers Traefik routers competing with the original's, on a host
already running them: two Compose projects of one name in different
directories, and two routers answering for one hostname. A missing value must
fail, not resolve to somebody else.

### `traefik-labels` — every routed service says how to reach it

A service carrying any `traefik.http.*` label must also carry
`traefik.enable=true` and `traefik.docker.network=web`. The second is not
boilerplate: a container on more than one network has to name the one Traefik
should dial, and without it Traefik picks one — sometimes `data`, where it is
not.

### `traefik-names` — router and service names derive from the slug

Checked by rendering the Stack with a slug no Project could have written, and
requiring every Traefik router, service and middleware name to have moved
with it. A derived name moves; a typed one does not. A literal name is a
collision waiting for the second Project that copies the file, and two
routers of one name is one Project answering for another.

### `networks` — `web` always, `data` only with the database Add-on

Both networks are created by the platform and joined by a Stack, never
created by one: a `web` that is not `external: true` is a network of the
Project's own that Traefik is not on, and the Stack comes up unreachable.

`data` is separate from `web` because `web` is where Traefik routes — every
Project's frontend sits on it — and a database server does not belong within
reach of all of them. A Stack reaches the shared Postgres because its
Manifest declares a database, or it does not reach it at all.

### `healthchecks` — every service declares one

A container that is running is not a container that is working. Without a
healthcheck, a deploy's only evidence is that Docker started the process, and
a Stack that comes up broken looks exactly like a Stack that came up.

### `no-env-file` — no service reads a file of values

Secrets are injected into the environment of the process that starts the
Stack, and no file of values is written on the host — so there is none to
leak, none to go stale, and none to be left behind by a deploy.

Read from the source, and the one deliberate exception to judging the
rendered file: `docker compose config` folds an `env_file` into `environment`
and erases the key, so after rendering, a Project reading a file on disk is
indistinguishable from one that is not.

### `no-committed-env` — no `.env` in the repository

The one mistake that puts a real secret somewhere it can never be removed
from. Checked against what git tracks, not against what is in the directory:
a developer's own ignored `.env` is correct and must not fail anybody's
check. `.env.example` is allowed and encouraged — naming the keys is how a
Project stays legible to somebody who holds none of the values.

### `multi-stage-dockerfiles` — build in one stage, run in another

Image size is a contract requirement rather than an optimisation. The
registry's free-plan transfer quota is counted against every pull the
production host makes, and a single-stage image ships the build tree —
compilers, dev dependencies, caches — to production and over that quota on
every deploy.

## Reported, never enforced

**Image size.** The checker names the images a Stack would pull, and fails on
nothing. The number in circulation is not the number that matters: the
argument for small images comes from a 200 MB illustration against a
1 GB/month transfer quota, the first Project's backend image is 582 MB on
disk, and the quota counts *compressed layers over the wire* — which nobody
has measured. A ceiling set from an illustration would reject Projects for
the wrong reason.

It does not weigh them either, which is the honest half. A repository
contains no images, so there is nothing here to put on a scale, and the bytes
a laptop happens to have are neither the number the open question needs nor a
number CI could agree with. When a pull has actually been measured, this
becomes a rule and the contract's version changes.

## Versions

Pinned by a moving major tag. `@v1` is the current `v1.x.y`, so a Project
pinned at `@v1` picks up fixes without doing anything.

Changing a rule is a version bump. A change that would newly refuse a Project
which passes today is a **major** bump, and Projects move to it deliberately,
one at a time — so nothing starts failing on a morning when nobody touched
it.
