---
name: fuse-dev-push
description: Use when a Fuse branch is ready for dev review — committing a feature branch, merging it into `dev`, and pushing `dev` for the society project, vendor/imarc/cpa-tech, or vendor/imarc/inkwell.
---

# Fuse Dev Push

Stage a Fuse feature branch for dev review: commit it, push it, rebuild local `dev` from `origin/dev`, merge the branch, push `dev`.

The society root, `vendor/imarc/cpa-tech`, and `vendor/imarc/inkwell` are separate repos. Run every step inside the repo that holds the change (`git -C <repo>`), and repeat the whole sequence per repo when a change spans several. Use the `origin` remote in all three.

## Steps

1. **Check the branch.** Confirm `git branch --show-current` is the feature branch, never `dev`, `testing`, `release`, or `next`. Composer can move HEAD in vendor checkouts, so check vendor repos every time. Done when the repo is on the feature branch.
2. **Commit.** Follow `working-with-git`: inspect `git status` and `git diff`, stage only the change's files by path, commit. Done when `git status -s` shows nothing left that belongs to this change.
3. **Push the branch.** `git push -u origin <branch>`. Done when the branch tracks `origin/<branch>`.
4. **Rebuild `dev`.** `git fetch origin`, then `git log --oneline origin/dev..dev`. Local commits here are lost by the reset: list them for the user and stop until they decide. Otherwise run `git checkout dev && git reset --hard origin/dev`. Done when `dev` matches `origin/dev`.
5. **Merge.** `git merge --no-ff --no-edit <branch>`. On conflicts, use `resolving-merge-conflicts`. Then check `git diff --stat HEAD^1 HEAD`: the files must be only the branch's changes. Extra files mean the branch base carries commits `dev` lacks; show them to the user before pushing. Done when the merge commit holds only the branch's changes.
6. **Push `dev`.** `git push origin dev`, then `git checkout <branch>`. Done when `origin/dev` contains the merge commit and the repo is back on the feature branch.

## Package Follow-Up

After pushing `dev` in cpa-tech or inkwell, check `git diff --stat origin/dev^1 origin/dev` for files under `plugin/`. If any changed, tell the user the society needs a local `composer update` before its `dev` gets those files:

1. In the society root, on `dev`, the user runs `composer update imarc/cpa-tech` (or `imarc/inkwell`). `imarc/opus` copies new mapped plugin files and prompts before overwriting existing local copies, so the user runs it interactively (`! composer update imarc/cpa-tech`).
2. Commit the copied plugin files and `composer.lock` to the society `dev`, then `git push origin dev`.

Say this to the user explicitly, naming the package and the changed plugin files. The society `dev` lacks the change until the user completes it.
