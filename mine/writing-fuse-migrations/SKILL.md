---
name: writing-fuse-migrations
description: Use when a Fuse CPA site needs a Doctrine schema change — adding, altering, or removing a mapped property, index, or table on an entity in vendor/imarc/cpa-tech, vendor/imarc/inkwell, or a society's local entities.
---

# Writing Fuse Migrations

Apply only to a Fuse CPA-Tech project: root `bin/hiraeth` exists and `composer.json` requires `imarc/cpa-tech`.

Migrations are generated in the society checkout because that is where the database is, but almost none belong to it. Let the ORM author the DDL, then move the file to the package that owns the entity. Societies receive it at opus reconciliation.

## Gates

1. **`iwmigrate:*`, never `migrations:*`.** Both are registered; `migrations:*` targets the wrong version table and fails.
2. **Ask the user to confirm the database is fully migrated, then wait.** Never run `iwmigrate:migrate` to make it true — a stale diff captures other people's pending changes.
3. **Move the file, never copy.** Nothing new stays in `database/default/migrations/`; never stage that path.
4. **Keep the generated artifact.** Do not re-author DDL, rename the class, or round the timestamp.

## Procedure

`source ops-commands.sh` first.

1. **Baseline, before editing any entity:**
   ```sh
   ops hiraeth iwmigrate:status   # New Migrations: 0, Executed Unavailable Migrations: 0
   ops hiraeth iwmigrate:diff     # must throw NoChangesDetected
   ```
   `iwmigrate:diff` has no dry-run; the thrown `NoChangesDetected` is the pass. A generated file means pre-existing drift — delete it, report it, stop. Then ask the user to confirm, and wait.
2. **Edit the owning package's entity** (see Ownership). Never a `local/entities/` or `plugin/local/entities/` subclass — those declare only `@Entity`/`@Table`.
3. **`ops hiraeth iwmigrate:diff`** — writes `database/default/migrations/VersionYYYYMMDDHHMMSS.php`.
4. **Review.** New columns nullable or defaulted so existing rows stay valid. Add a real `getDescription()`, a working `down()`, and any backfill the ORM cannot infer. Filename, class name and DDL stay as generated.
5. **Apply and prove the mapping agrees:**
   ```sh
   ops hiraeth iwmigrate:migrate
   ops hiraeth iwmigrate:diff     # must throw NoChangesDetected again
   ```
   A file here means the annotation disagrees with the SQL — fix the annotation, not the migration. Re-test with `iwmigrate:execute --down <version>`; never touch the `migrations` table or `ALTER TABLE` by hand.
6. **Move** — plain `mv`, not `git mv` (source is untracked, `/vendor` is gitignored):
   ```sh
   mv database/default/migrations/VersionYYYYMMDDHHMMSS.php \
     vendor/imarc/<package>/plugin/database/default/migrations/
   ```
   `git status --short database/` must then be empty.
7. **Commit in the package repo only**, on a branch off its remote `release`. Never hand-edit `opus.map`; opus regenerates it on install.

`iwmigrate:status` now reports `Executed Unavailable Migrations` above zero. Expected — the versions are in the database, the files are in the package, and reconciliation resolves it. Do not copy the files back.

## Ownership

The owner is the class that **declares the mapped property** — not the table name, not where the concrete entity lives. `Inkwell\Person` → `CpaTech\Person` → the society's `Person` share one `people` table; a column on `Inkwell\Person` is Inkwell's.

| Declared on | Migration goes to |
| --- | --- |
| `imarc/inkwell/src/data/entities/…` | `imarc/inkwell/plugin/database/default/migrations/` |
| `imarc/cpa-tech/src/data/entities/…` | `imarc/cpa-tech/plugin/database/default/migrations/` |
| only the society's `local/entities/…` | stays in `database/default/migrations/`, committed there |

`opus.map` records who owns each existing migration. One with no entry is stranded and will never reach another society — report it.

## Both Packages

One file per package, never combined — an Inkwell-only site has no CPA-Tech tables. Generate sequentially instead of hand-splitting: Inkwell entity → diff → review → migrate → clean diff, then the same for CPA-Tech. Going Inkwell-first gives it the earlier timestamp, which is the required order; merge its MR first too, since cpa-tech requires `imarc/inkwell: dev-release`. Hand-split only a single statement the generator cannot separate.

## Mistakes

| Mistake | Consequence |
| --- | --- |
| `migrations:*` instead of `iwmigrate:*` | Wrong version table; the command fails |
| Diffing before the database is current | Other people's pending changes land in your migration |
| Skipping the pre-edit baseline diff | Unrelated drift ships inside your migration |
| `cp`, or staging `database/default/migrations/` | Commits a file reconciliation is meant to deliver |
| `git mv` | Fails: untracked source, gitignored destination |
| Re-authoring the migration by hand | Diverges from the ORM; the next diff is dirty |
| Raw SQL against the `migrations` table | Desyncs the database from migration history |
| One combined cross-package migration | Inkwell-only sites break on CPA-Tech tables |
| Property added to `local/entities/` | Ships to one society; opus prompts to overwrite |
