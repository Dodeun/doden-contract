# What production refuses, and which rule says so first

A Project is deployed by `prod-deploy` on the production host, the forced
command of that Project's own deploy key. It renders the Project's
`docker-compose.prod.yml` in a sandbox and holds the render to a list of its
own,
[`prod-deploy/compose-policy.jq`](https://github.com/Dodeun/doden-infra/blob/main/prod-deploy/compose-policy.jq)
in `doden-infra`. The deploy user is in the `docker` group, which is root,
and the file was written by whoever could push a tag, so the list names what
a Project may ask for and refuses the rest. `check.py` reads the same render,
so a Project production would refuse fails its check first, on its pull
request, and not at its deploy, after a release tag that cannot be moved.

**This table is where the two meet.** One row per refusal of
`compose-policy.jq`, read at `doden-infra` commit `33409b3`, with the rule
of `check.py` that catches the same thing in a repository. A refusal added
to either side gets its row here, and the other side follows.
`tests/test_prod.py` builds, for every row, a Project that the row
describes, and checks that `check.py` refuses it with that rule and no
other. A row without a test fails the suite.

Most rows are the `prod-*` rules of [`contract/prod.py`](../contract/prod.py).
They hold **a Project only**, a Manifest with no `prototype` block: a
Prototype never deploys to production, `prod-deploy` refuses its Manifest,
and it is held to the lab's list instead, in [`lab.md`](lab.md). Where a
rule of the contract's already catches a refusal, in every Project, that
rule is the row's, and the `prod-*` rules do not repeat it.

**Production's list is the lab's, with four differences**, so the two
tables read row for row:

1. `data` keeps its name; the lab's is `lab-data`. A Project's `data`
   external and named `lab-data` is refused (N2).
2. A router, Traefik service or middleware may be named `<slug>-<suffix>`
   as well as `<slug>_<suffix>`. A person chooses every Project's slug, and
   `prod-pin-key` refuses a slug that begins another's, followed by `-`
   (below).
3. A router answers for `Host` of the key's hostname. The checker cannot see
   the key, and reads the Manifest's `appHost` instead: `prod-deploy`
   refuses a Manifest whose `appHost` is not the key's.
4. Every image is `ghcr.io/dodeun/<slug>-<name>`, at the commit the request
   deploys (S6b, S6c).

And no `@file` middleware, since production's Traefik has no file provider,
and the host's own containers have other names.

The checker renders with its sentinels for the slug, the hostname, the image
prefix, the tag and the release, and the host with the real ones, and
neither with anything else of the shell that runs it. So a key written with
no value is null in both renders, a secret taken from Doppler, unless it is
one of those five, which the render fills. A name built from
`${PROJECT_SLUG}` behaves the same in both renders. Where the host would
accept a literal, so does the checker: the slug, the hostname and the image
prefix as the Manifest has them. A literal Traefik name is `traefik-names`'s
to refuse, before the `prod-*` rules see it.

## The file

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| F1 | a top-level key other than `name`, `services`, `networks`, `volumes`, including an `x-` extension, which the render keeps | `prod-file` |
| F2 | a `name` that is not the slug | `prod-file` |
| F3 | a file with no service | `prod-file`, and `networks`, since nothing joins `web` |
| F4 | `include`: the sandbox renders the file alone, from stdin, so the other file is not there, or is one of the render image's own | `prod-file`, from the source |
| F5 | `prod-deploy` and `prod-pin-key`, not the policy: a slug that is one of the host's own Compose projects or databases, `postgres`, `template0`, `template1`, `traefik`, `placeholder`, `maintenance` | `prod-file`, from the Manifest |
| F5b | the same, for a slug beginning `pg-`, which the platform keeps for itself | `prod-file`, from the Manifest |
| F6 | `extends` naming another file, as F4 | `prod-file`, from the source |

## Networks

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| N1 | an external network that is not `web` or `data` | `networks` |
| N2 | `web` or `data`, external, named neither `web` nor `data`: `lab-data` among them | `prod-networks`: `networks` reads the key, the host the name |
| N3 | a network of the Stack's own given a name | `networks` |
| N4 | `data` without the database Add-on | `networks` |
| N5 | a key other than `name`, `external`, `ipam`, and `internal` for one of the Stack's own | `prod-networks` |
| N6 | an `ipam` that is not empty | `prod-networks` |

## Volumes

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| V1 | a key other than `name`, `labels`, `driver`, `driver_opts`, `external` | `compose-file`: Compose itself refuses to render any other, and drops an `x-` one |
| V2 | an external volume | `prod-volumes` |
| V3 | `driver_opts`, which can bind a directory of the host | `prod-volumes` |
| V4 | a driver other than `local` | `prod-volumes` |
| V5 | a volume named in the file | `prod-volumes` |

## Services

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| S1 | a service named `traefik`, `postgres`, `placeholder` or `maintenance` | `prod-services` |
| S1b | a service name outside lower case letters, digits, `_`, `.` and `-`, or not starting with a letter or a digit: `-worker` would read as an option of `docker compose run` | `prod-services` |
| S2 | a key outside its list, such as `privileged`, `cap_add`, `network_mode`, `pull_policy` | `prod-services` |
| S3 | `ports` | `no-published-ports` |
| S4 | `build` | `no-build` |
| S5 | `env_file`: the sandbox renders the file alone, as F4 | `no-env-file`, from the source |
| S6 | a service with no image | `compose-file`: Compose refuses to render it |
| S6b | an image outside `ghcr.io/dodeun/<slug>-`: another registry, or another Project's | `prod-services` |
| S6c | an image pinned by digest, which is not the commit the request deploys | `prod-services` |
| S6d | an image at a tag that is not the commit | `image-tag` |
| S7 | `user` root, by name or number, as the user or the group | `prod-services` |
| S7b | `user` `-0`, a signed zero, which Docker reads as uid 0 too | `prod-services` |
| S8 | a service that joins no network | `prod-services` |
| S9 | a network the file does not declare | `compose-file` |
| S10 | joining a network with settings: aliases, an address, a priority | `prod-services` |
| S11 | a named volume the file does not declare | `compose-file` |
| S12 | an anonymous volume | `prod-services` |
| S13 | a mount that is not a volume or a tmpfs: a bind, the Docker socket among them | `prod-services` |
| S14 | a volume mounted with an option other than `nocopy`: `subpath` among them | `prod-services` |
| S15 | a secret, an environment key with no value that the render does not fill, that is not the name of an environment variable | `prod-services` |
| S16 | a secret of a name docker, bash or the deploy itself reads: `PATH`, `HOME`, `DOCKER_HOST`, `DOCKER_CONFIG`, `LD_PRELOAD` and the like | `prod-services` |

## Labels

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| L1 | a `com.docker.` label, which says whose container this is | `prod-labels` |
| L2 | `traefik.enable` other than `true` or `false` | `prod-labels` |
| L3 | `traefik.docker.network` other than `web`, on a routed service | `traefik-labels` |
| L3b | the same label in another case, which the policy reads in lower case | `prod-labels` |
| L4 | `traefik.docker.network` other than `web`, on a service with no router | `prod-labels` |
| L5 | a router's or a Traefik service's label not in lower case | `prod-labels` |
| L6 | `traefik.http.routers.<name>` with nothing after the name | `prod-labels` |
| L7 | a router, Traefik service or middleware built from the slug but not named after it: `api-${PROJECT_SLUG}` | `prod-labels` |
| L8 | the same, named by a literal | `traefik-names` |
| L8b | a middleware's label in another case, named by a literal: `traefik-names` reads labels as written | `prod-labels` |
| L9 | a router's rule other than `Host` of its hostname, with one `Path` or `PathPrefix` at most | `prod-labels` |
| L10 | entrypoints other than `web` and `websecure` | `prod-labels` |
| L11 | a router's middleware that is not its own: `noindex@file` among them | `prod-labels` |
| L12 | a router's service that is not its own | `prod-labels` |
| L13 | a priority that is not a number | `prod-labels` |
| L14 | `tls` other than `true` | `prod-labels` |
| L15 | a certresolver other than `letsencrypt` | `prod-labels` |
| L16 | any other router label | `prod-labels` |
| L17 | `traefik.http.services.<name>` with nothing after the name | `prod-labels` |
| L18 | a Traefik service's port that is not a number | `prod-labels` |
| L19 | a scheme other than `http`, `https`, `h2c` | `prod-labels` |
| L20 | any other Traefik service label | `prod-labels` |
| L21 | `traefik.http.middlewares.<name>` with nothing after the name | `prod-labels` |
| L22 | a middleware type outside the list: `basicauth` and `forwardauth` among them | `prod-labels` |
| L23 | any other `traefik.` label: TCP, UDP, a transport | `prod-labels` |
| L24 | a router with no rule, for which Traefik would make one up | `prod-labels` |
| L25 | `traefik.enable=true` with no router, for which Traefik would make one up | `prod-labels` |

## What production refuses that a repository cannot show

`prod-deploy` refuses more than the policy, before and after it. A
repository can show what follows from its own files:

- the Manifest's shape, the database Add-on's `migrate` command among it:
  `manifest`;
- the migrate command's Service: `addon-commands`;
- a slug the host keeps for itself: `prod-file` (F5);
- a `prototype` block: a Prototype is held to the lab's rules and never
  reaches production, and a promotion removes the block;
- a file that does not render with only the values the platform supplies:
  `compose-file`. The sandbox renders it with `PROJECT_SLUG`, `APP_HOST`,
  `IMAGE_REPO_PREFIX`, `IMAGE_TAG` and `RELEASE_TAG` alone, from stdin, so a
  file next to it is not there either: an `env_file` (S5), an `include`
  (F4) or an `extends` naming a file (F6) is refused from the source.

These refusals depend on things outside the repository:

- **the key and the request:** the deploy key is pinned to one slug and one
  hostname; the request names that slug, is version 1, is no bigger than
  256 KiB, and carries the two files with their hashes; the Manifest's slug
  and `appHost` are the key's. The checker reads the Manifest's `appHost`,
  and the host's comparison is the real one;
- **the other Projects:** `prod-pin-key` refuses a slug that begins
  another pinned slug followed by `-`, or that another begins so: Traefik
  merges same-named services across containers, and `jobs` could otherwise
  name a service `jobs-finder-api`;
- **the images:** they exist in GHCR at the commit, and the job's token can
  pull them;
- **Doppler:** a token registered for the Project's own directory when its
  Stack asks for a secret, and not one inherited from a parent;
- **the operator's memory ceilings** in `/etc/prod-deploy/memory`, which
  restrain and do not refuse.

**An older Release is accepted** (decision 69): an image that runs as root
is not refused, or a rollback would be refused at the worst moment. The list
refuses root asked for in the Compose file, never an image's own user.
