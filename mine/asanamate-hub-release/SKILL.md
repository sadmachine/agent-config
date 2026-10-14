---
name: asanamate-hub-release
description: Use when tagging or pushing an asanamate release tag (`vX.Y.Z`), to tag imarc/asanamate-hub with the same version.
---

# asanamate-hub release

`asanamate-hub` is imarc's Hub provider for asanamate's project links. It lives in the private `imarc/asanamate-hub` repository, and its tags track asanamate's: every asanamate release tag gets the same tag there. The hub has no Go dependency on asanamate, so a release needs no code change in it.

Run this after asanamate's own release steps (its `AGENTS.md`, "Version bumps") have pushed the asanamate tag.

## Steps

1. **Find the clone.** Use `~/tools/asanamate-hub`; if it is missing, clone `https://github.com/imarc/asanamate-hub.git` there. Done when `git -C ~/tools/asanamate-hub remote get-url origin` names `imarc/asanamate-hub`.
2. **Update `main`.** `git checkout main && git pull --ff-only origin main`. Uncommitted or unpushed work in the clone belongs to the user: list it and stop until they decide. Done when `main` matches `origin/main`.
3. **Check the protocol.** `protocol.go` mirrors the project links protocol in asanamate's `docs/extensions.md`. Compare it with `git -C <asanamate> diff <previous tag>..<new tag> -- docs/extensions.md`; if the protocol changed, tell the user the hub needs an update before its tag, and stop. Done when the protocol is unchanged or the user has decided.
4. **Tag and push.** `git tag -a vX.Y.Z -m vX.Y.Z`, then `git push origin vX.Y.Z`, using asanamate's exact tag. Done when `git ls-remote --tags origin vX.Y.Z` shows it.

If the repository is unreachable, tell the user to tag it themselves, naming the version.
