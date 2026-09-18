---
name: working-with-fuse
description: Use ONLY when the local project root has `bin/hiraeth` and `composer.json` requires `imarc/cpa-tech`; then use for Fuse templates, PHP classes, assets, CPA society behavior, or vendor/imarc/cpa-tech and vendor/imarc/inkwell packages.
---

# Working With Fuse

Apply this skill only to a confirmed Fuse CPA-Tech project: root `bin/hiraeth` exists and root `composer.json` requires `imarc/cpa-tech` (in `require` or `require-dev`). Do not infer Fuse from PHP, Composer, Hiraeth-like files, Inkwell references, directory names, or vendor packages alone.

## Documentation

**REQUIRED SUB-SKILL:** Use `working-with-fuse-outline` before reading or writing any Fuse documentation in Outline. It scopes all Outline work to the Fuse root page and its descendants.

## Scope And Ownership

Choose the narrowest layer that owns the behavior:

| Layer | Use for |
| --- | --- |
| `vendor/imarc/inkwell` | Reusable non-CPA framework, primitives, infrastructure, or app behavior |
| `vendor/imarc/cpa-tech` | CPA society behavior shared by CPA sites |
| Local project | One society's config, content, branding, templates, or customization |

The hierarchy is `Inkwell > CPA-Tech > local project`; narrower is safer. For ambiguous local vs xplat work, ask: "This looks [society-specific/xplat] because [reason]. Should I implement it [locally/as xplat]?" For xplat work, also ask CPA-Tech vs Inkwell and recommend CPA-Tech for CPA-domain behavior, Inkwell otherwise. Membership renewal reminders usually belong in CPA-Tech; a homepage hero animation is usually local.

Prefer the owning class, service, template, command, or config surface over a wrapper, subclass, parallel service, or local override. Before adding one, ask whether an existing owner can be extended more simply. Additive optional methods/config and preserved output are usually compatible. Renames, constructor/return-type changes, default changes, or required config may break compatibility: stop and ask, explaining the risk and an additive method, nullable/defaulted argument, adapter, alias, or deprecation alternative.

## Asana And Branches

**REQUIRED SUB-SKILL:** Use `working-with-asana` before posting a comment to, or otherwise writing to, any Asana task. Asana writes are draft-then-confirm: print the exact content, ask, and stop.

When an Asana link is provided at any point, retrieve its task and read the `ID` field from CPA Product: Fuse, plus `Branch Name`. `ID` values are prefixed `CPA-`. For local and xplat branches, use `Branch Name` when present; otherwise, if `ID` is present, use `<update|bugfix|feature>/<number>-<two-to-three-word-summary>` (for example, `update/123-alert-logic`). Make sure to always branch off of the remote `release` branch, and always set upstream to match the local branchname.

## Before Editing

**REQUIRED SUB-SKILL:** Use `writing-fuse-migrations` for any Doctrine schema change. Never hand-write a migration.

1. Confirm the two Fuse conditions above; check root status and vendor repo status for every package that may change.
2. Search local, CPA-Tech, and Inkwell for the relevant template, class, config, plugin, script, or style; trace active runtime resolution before choosing a file.
3. Treat root, CPA-Tech, and Inkwell as separate repos with separate branches, diffs, tests, and commits. Branch inside a vendor repo when changing it.

## Implementation Details

Name every new internal `.jin` key in camelCase. When a key directly maps required snake_case or kebab-case syntax, match that syntax exactly.

| Area | Check | Rule |
| --- | --- | --- |
| Templates | Local `config/packages/templates.jin`, local `--extends = file(vendor/...)`, `vendor/imarc/{cpa-tech,inkwell}/config/*.jin`, aliases `@layouts`, `@pages`, `@cpa-tech`, `@inkwell` | Trace aliases, Twig includes/extends, and `.jin` layering. |
| PHP | Local `local/` classes extending vendor abstracts (for example, `Page extends Inkwell\Page` or `FirmType extends CpaTech\FirmType`), parent class, autoload/config, manager/tool config | Inspect the whole inheritance and configuration path before editing. |
| Plugin files | Both package `plugin/` folders, root `opus.map`, package `composer.json` mappings, local copy vs package source | `imarc/opus` copies missing mapped files on Composer install/update and prompts for existing ones. Edit package `plugin/` for future defaults; edit a local copy for one society. Package edits do not update existing local copies. |
| JS/CSS | `vite.config.mjs`, `package.json`, `resources/styles/main.scss`, aliases `@inkwell`, `@cpa-tech`, `@resources`, `resources/styles/**/local/` | Preserve import order unless changing the cascade: local variables, vendor variables, vendor/local mixins, resets, base, atomic, utilities. |

## Red Flags And Verification

- Do not edit CPA-Tech for one-society branding/content, Inkwell for CPA-specific language/workflow, or an ambiguous/xplat scope without the required question and recommendation.
- Do not edit templates without resolution tracing, copied plugins without `opus.map`, package plugins expecting local copies to update, or assets without build config and import-order checks.
- Do not report one git status for nested repos.

Discover commands from `composer.json`, `package.json`, Makefiles, or docs. For each touched repo, inspect status and diff, then verify the relevant runtime path: rendered template, resolved class, copied plugin, built asset/import order, config cache, generated output, migration, or Composer/Opus behavior.
