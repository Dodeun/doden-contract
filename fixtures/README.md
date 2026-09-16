# Fixtures

A rule with no failing example is a rule that has never run.

`base/` is a conforming Project with the database Add-on — two services, both
routed, one of them on `data`. It is deliberately the smallest Stack that
still gives every tier-1 rule something to judge.

Everything under `pass/` and `fail/` is an **overlay** on it: only the files
that differ. The harness copies `base/` into a temporary directory, copies
the overlay over it, and runs the checker. So the fixture is the diff, and
reading `fail/published-ports/docker-compose.prod.yml` beside `base/`'s tells
you exactly what the rule objects to.

- `.remove` in an overlay lists paths to delete from the merged tree, one per
  line. That is how "this file is missing" is written.
- `CONTRACT.md` is copied in from the repository root, exactly as the
  platform copies it into a Project, unless an overlay supplies its own.
- An overlay writes its `.gitignore` as **`dot-gitignore`**, which the
  harness renames. A real one would apply to this repository as well, and
  would quietly stop `pass/ignored-env/.env` from ever being committed -
  leaving a fixture that demonstrates nothing and a suite that stays green
  while it does.
- The merged tree is `git init`ed and staged, because "no committed `.env`"
  is a question about the index rather than about the directory.

`pass/` trees must come back clean. `fail/` trees must fire exactly one rule
— if a fixture fires two, either the fixture is doing too much or two rules
overlap, and both are worth knowing.

Some fixtures exist to demonstrate something beyond their own rule:

| Fixture | What it is really about |
| --- | --- |
| `fail/published-ports-through-a-variable` | The source file contains no port number at all. This is why the rendered file is what gets judged. |
| `fail/traefik-names-literal` | Names that render perfectly and collide with the next Project. Caught by rendering with a slug no Project could have typed. |
| `fail/env-file` | The render *erases* `env_file`, folding it into `environment`. The one rule that has to read the source. |
| `pass/ignored-env` | A developer's own `.env`, ignored. Correct, and must never fail anybody's check. |
| `pass/no-database` | The database Add-on off, and the `data` network gone with it. The contract has to pass both ways. |
| `fail/image-tag-floating` | A tag that is not `latest` and still floats. A denylist of obvious names lets `v1` through - and this contract's own release process moves `v1`. |
| `fail/healthcheck-disabled` | `disable: true` reads as configuration rather than as removal, which is what makes it the realistic evasion. |
| `fail/network-data-missing` | The Add-on declared and the network absent. The `networks` rule reads both ways, so it needs a fixture in both. |
