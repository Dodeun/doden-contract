# The `database` Add-on

Contract version: `v2`

```json
"addons": { "database": { "provider": "postgresql" } }
```

This document is canonical in
[`Dodeun/doden-contract`](https://github.com/Dodeun/doden-contract), beside
`CONTRACT.md`, and is versioned with the schema — so a copy can never
describe a contract version it does not ship with. It is copied into a
Project that declares this Add-on and **removed from one that does not**: a
Project carrying a document for a capability it has not got is describing
something that is not there.

It says what declaring the Add-on commits you to. It is not a guide to your
ORM.

## What you get

**One Postgres server, shared** (ADR-0005). Not a container in your Stack —
the platform runs one engine for every Project, on the `data` network, and
your Stack joins it. A Stack that starts a database of its own is two
engines on one small machine, which is what this decision exists to prevent.

**A role and a database of your own**, named after your slug with
underscores where the slug has hyphens (`jobs-finder` → `jobs_finder`). The
role **owns** the database rather than merely having access to it: since
Postgres 15 only the owner may create in the `public` schema, so a role with
access alone fails on its first migration with `permission denied for schema
public`.

**`DATABASE_URL`, in the Doppler project the platform created for you**,
pointing at host `postgres` over the `data` network. Nobody types it and
nobody reads it back — it is generated when the Project is created and
written straight into the secrets manager (ADR-0003, ADR-0007). It reaches
your containers through the environment of the process that starts the Stack;
there is no file of values on the host.

**A place in the nightly backup, without asking anyone.** The backup
enumerates every database on the engine, so yours is in tomorrow morning's
dump and in the 30 days of history behind it, off the machine, in an
append-only bucket. You do not register anything and there is nothing to
forget. A Backup restores *state*; it does not restore behaviour, and a
Rollback does not restore data — those are three different acts and it is
worth keeping them three different words.

## What you owe

**One provider, and it is `postgresql`.** The key exists so that the Manifest
stops asserting, in its own shape, that there could never be a second engine.
It is not an invitation to choose one: the `addon-provider` rule refuses
anything the platform does not run, and a second value there is a change to
ADR-0005 rather than a line in your Manifest.

**`DATABASE_URL` in the Stack, and only when you declare this.** The
`database-url` rule reads both ways — declared and not passed is a Stack that
connects to nothing; passed and not declared is a Project nobody created a
role for. The same is true of the `data` network, which the `networks` rule
checks in both directions for the same reason.

**`seedCommand`, in the Manifest, and it has to work.** It is what fills an
empty database with **synthetic** data for a Preview. Never a copy of
production: that is what keeps a Preview and the people testing it outside
production's Trust Domain. Your Profile runs it on every CI run rather than
the first time a Preview needs it, because a declared command nobody has run
is a Manifest field that is false.

**Migrations applied forward, by the deploy, before the new images serve.**
The Profile supplies the command — `prisma migrate deploy` in `node-web` —
and the shape is the contract's: forward only. There is no down-migration in
this platform and a Rollback does not run one.

That makes **expand/contract** an obligation on the migration rather than a
step in the rollback. Add the column, write both, backfill, and drop the old
one in a *later* release: an older image has to be able to run against the
newer schema, because a Rollback re-points the Stack at previously published
images and does nothing at all to the database. A Rollback across an additive
migration is uneventful. Across a renamed or dropped column it is a fresh
outage, and the only way out of that one is forward.

**A local database in development is yours.** The shared engine is
production's. The Profile ships a Compose file that starts one on your
machine, and what you do to it is nobody else's business — the contract has
no opinion about it, and the production Stack is the only Stack it judges.

## What this Add-on is not

It is not your schema, your ORM, your query layer or your data model. Those
are the Project's, and the platform has nothing to say about them.

It is also not a reason to ask for a second engine. An Add-on is an optional
dependency on a service the platform **operates**; asking for an engine
nobody runs is asking somebody to run it.

## If you remove it

Take out the Manifest key, the `data` network, the `DATABASE_URL`, the seed
command and this file. The role, the database and the secret are the
platform's to remove, and `project delete` is where that lives — but the
dumps already taken stay in the backup bucket, because the backup key holds
no `deleteFiles` on purpose.
