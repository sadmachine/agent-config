---
name: working-with-git
description: Use when making code changes that need to be committed, to ensure atomic commits with line-level granularity and explicit confirmation before staging.
---

# Working With Git

Make commits atomic: one logical change per commit, granular to the line level when edits to the same file serve distinct purposes.

## Rules

1. **One logical change per commit.** If you make multiple edits to a file that address separate concerns, commit them separately.
2. **Ask before staging and committing.** Never `git add` or `git commit` without explicit user confirmation.
3. **Stage only what belongs to the commit.** Use `git add -p` or file-specific adds; never `git add .` or `git add -A`.
   In a non-interactive session `git add -p` is unavailable: use the `staging-hunks` skill to split a file by hunk.

## Before Committing

- `git status` and `git diff` to inspect all changes
- Group changes by concern, not by file
- Verify no secrets, keys, or credentials are staged

## Quick Reference

| Situation | Action |
| --- | --- |
| Same file, two unrelated fixes | Two commits |
| Related changes across files | One commit |
| Formatting + logic change | Two commits |
| Unsure if changes belong together | Ask user |
| File has both staged and unstaged changes | Review before adding more |

## Red Flags

- Committing everything at once because "it's all related"
- Using `git add .` or `git add -A`
- Committing without asking
- Combining formatting/refactoring with behavior changes
