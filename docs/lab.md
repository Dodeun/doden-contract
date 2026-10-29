# What the lab refuses, and which rule says so first

A Prototype is deployed by the control host's `lab-deploy`, which renders its
`docker-compose.prod.yml` and holds the render to a policy of its own,
[`lab-deploy/compose-policy.jq`](https://github.com/Dodeun/doden-control-host/blob/main/lab-deploy/compose-policy.jq)
in `doden-control-host`. The deploy user can do anything Docker can, which
is root, so the policy lists what a Prototype may ask for and refuses the
rest. `check.py` reads the same render, so a Prototype the lab would refuse
fails its check first, on its pull request, not on its first deploy. Ticket
47's trial had two first deploys refused that the checker had passed.

**This table is where the two meet.** One row per refusal of
`compose-policy.jq`, read at `doden-control-host` commit `2545076`, with the
rule of `check.py` that catches the same thing in a repository. A refusal
added to either side gets its row here, and the other side follows.
`tests/test_lab.py` builds, for every row, a Prototype that the row describes,
and checks that `check.py` refuses it with that rule and no other. A row
without a test fails the suite.

Most rows are the `lab-*` rules of [`contract/lab.py`](../contract/lab.py).
They hold **a Prototype only**, a Manifest with a `prototype` block,
because production has no such policy and accepts some things the lab
refuses: a router called `<slug>-api`, or an alias on a network of the
Stack's own. CONTRACT.md says why the lab is stricter. Where a rule of the
contract's already catches a refusal, in every Project, that rule is the
row's, and the `lab-*` rules do not repeat it.

The checker renders with its sentinels for the slug and the hostname, and
the lab with the real ones. A name built from `${PROJECT_SLUG}` behaves the
same in both renders. A literal is `traefik-names`'s to refuse, before the
lab rules see it.

## The file

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| F1 | a top-level key other than `name`, `services`, `networks`, `volumes`, including an `x-` extension, which the render keeps | `lab-file` |
| F2 | a `name` that is not the slug | `lab-file` |
| F3 | a file with no service | `lab-file`, and `networks`, since nothing joins `web` |
| F4 | `include`: the policy never sees it, because the render folds the files in, or fails on the lab, which renders the file alone | `lab-file`, from the source |

## Networks

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| N1 | an external network that is not `web`, `data` or `lab-data` | `networks` |
| N2 | `web` or `data`, external, named otherwise | `lab-networks`: `networks` reads the key, the lab the name |
| N3 | a network of the Stack's own given a name | `networks` |
| N4 | `data` without the database Add-on | `networks` |
| N5 | a key other than `name`, `external`, `ipam`, and `internal` for one of the Stack's own | `lab-networks` |
| N6 | an `ipam` that is not empty | `lab-networks` |

## Volumes

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| V1 | a key other than `name`, `labels`, `driver`, `driver_opts`, `external` | `compose-file`: Compose itself refuses to render any other |
| V2 | an external volume | `lab-volumes` |
| V3 | `driver_opts`, which can bind a directory of the host | `lab-volumes` |
| V4 | a driver other than `local` | `lab-volumes` |
| V5 | a volume named in the file | `lab-volumes` |

## Services

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| S1 | a service named `traefik`, `postgres-lab`, `access-code` or `hello` | `lab-services` |
| S2 | a key outside its list, such as `privileged`, `cap_add`, `network_mode`, `pull_policy` | `lab-services` |
| S3 | `ports` | `no-published-ports` |
| S4 | `build` | `no-build` |
| S5 | `env_file`: the policy never sees it, because the render folds it into `environment`, or fails on the lab, where the file is not | `no-env-file`, from the source |
| S6 | a service with no image | `compose-file`: Compose refuses to render it |
| S7 | `user` root, by name or number, as the user or the group | `lab-services` |
| S8 | a service that joins no network | `lab-services` |
| S9 | a network the file does not declare | `compose-file` |
| S10 | joining a network with settings: aliases, an address, a priority | `lab-services` |
| S11 | a named volume the file does not declare | `compose-file` |
| S12 | an anonymous volume | `lab-services` |
| S13 | a mount that is not a volume or a tmpfs: a bind, the Docker socket among them | `lab-services` |

## Labels

| Row | `compose-policy.jq` refuses | `check.py` |
| --- | --- | --- |
| L1 | a `com.docker.` label, which says whose container this is | `lab-labels` |
| L2 | `traefik.enable` other than `true` or `false` | `lab-labels` |
| L3 | `traefik.docker.network` other than `web`, on a routed service | `traefik-labels` |
| L4 | `traefik.docker.network` other than `web`, on a service with no router | `lab-labels` |
| L5 | a router's or a Traefik service's label not in lower case | `lab-labels` |
| L6 | `traefik.http.routers.<name>` with nothing after the name | `lab-labels` |
| L7 | a router, Traefik service or middleware built from the slug but not named after it: `${PROJECT_SLUG}-api` | `lab-labels` |
| L8 | the same, named by a literal | `traefik-names` |
| L9 | a router's rule other than `Host` of its hostname, with one `Path` or `PathPrefix` at most | `lab-labels` |
| L10 | entrypoints other than `web` and `websecure` | `lab-labels` |
| L11 | a router's middleware that is not its own or `noindex@file` | `lab-labels` |
| L12 | a router's service that is not its own | `lab-labels` |
| L13 | a priority that is not a number | `lab-labels` |
| L14 | `tls` other than `true` | `lab-labels` |
| L15 | a certresolver other than `letsencrypt` | `lab-labels` |
| L16 | any other router label | `lab-labels` |
| L17 | `traefik.http.services.<name>` with nothing after the name | `lab-labels` |
| L18 | a Traefik service's port that is not a number | `lab-labels` |
| L19 | a scheme other than `http`, `https`, `h2c` | `lab-labels` |
| L20 | any other Traefik service label | `lab-labels` |
| L21 | `traefik.http.middlewares.<name>` with nothing after the name | `lab-labels` |
| L22 | a middleware type outside the list: `basicauth` and `forwardauth` among them | `lab-labels` |
| L23 | any other `traefik.` label: TCP, UDP, a transport | `lab-labels` |
| L24 | a router with no rule, for which Traefik would make one up | `lab-labels` |
| L25 | `traefik.enable=true` with no router, for which Traefik would make one up | `lab-labels` |

## What the lab refuses that a repository cannot show

`lab-deploy.sh` refuses more than the policy, before and after it. A
repository can show what follows from its own files:

- the Manifest's slug, `prototype` block, hostname, expiry and access code:
  `manifest`, `prototype-host`, `prototype-expires`;
- the migrate command's Service: `addon-commands`;
- a file that does not render with only the values the platform supplies:
  `compose-file`. The lab renders it with `PROJECT_SLUG`, `APP_HOST`,
  `IMAGE_REPO_PREFIX`, `IMAGE_TAG` and `RELEASE_TAG` alone, from stdin, so a
  file next to it is not there either: an `env_file` (S5) or an `include`
  (F4) is refused from the source. An `extends` naming another file is not
  caught: no Template writes one, and the lab's render says why it fails.

These refusals depend on things outside the repository:

- **the key and the request:** the deploy key is pinned to one slug; the
  request names that slug, is version 1, is no bigger than its limit, and
  carries the files with their hashes;
- **the images:** they exist in GHCR under the commit, and the job's token
  can pull them;
- **the secrets file** on the host: one `NAME=value` per line, none of the
  names the deploy reads itself, and a `DATABASE_URL` when the Add-on is
  declared. `prototype new` writes it.

The policy's README, in `doden-control-host`, points here.

## The checker a working tree runs

CI calls the reusable workflow at `@v3` on every run, so it uses the
current `v3`. A local run uses whatever `~/.doden-contract` holds. Until
`v3.5.0`, CONTRACT.md cloned it **once**. `v3` moves with every release, and
nothing updated the clone. So `habits-sillage`'s agent in round 2 ran a
copy older than CI's, which refused what CI accepted. CONTRACT.md now
replaces the clone on every run, which takes a second. The Template's
`AGENTS.md` and README carry the old line. Ticket 56 updates the Template,
and takes those two files with it.
