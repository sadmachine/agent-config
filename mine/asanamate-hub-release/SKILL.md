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
3. **Sync with asanamate.** Each hub tag ships a hub that is current with asanamate's tag of the same version. Read `git -C ~/tools/asanamate diff <previous tag>..<new tag>` over every surface the hub depends on:
   - `docs/extensions.md`, the "Project links" section: the request, the response, link types (the hub's `protocol.go` mirrors them), and provider conventions such as `check` and stderr. Other extension sections, such as feeds, don't touch the hub.
   - `internal/projectlinks/`: what asanamate actually sends and accepts, where the doc may lag.
   - The `project_links.*` keys in `internal/config/config.go` and the README "Project links (optional)" section: the hub README's setup steps quote them.
   - README headings the hub README links to (`#install`, `#setup`, `#time-tracking-optional`) and its "asanamate vX.Y.Z or later" requirement.
   - The `go` line in `go.mod`: keep the hub's on the same version.

   For each change the hub must follow (a new request field it can use, a renamed key, a moved link target), update the hub on `main`, run `go test ./...` and `go vet ./...`, commit it as a conventional commit, and `git push origin main`. Raise the hub README's minimum asanamate version when the hub starts relying on something new. A change you can't map onto the hub, or one that changes what the hub does for users, goes to the user before you edit. Done when every changed surface is either followed by a pushed hub commit or confirmed to need nothing, and you can name which.
4. **Tag and push.** `git tag -a vX.Y.Z -m vX.Y.Z`, then `git push origin vX.Y.Z`, using asanamate's exact tag. Done when `git ls-remote --tags origin vX.Y.Z` shows it.

If the repository is unreachable, tell the user to tag it themselves, naming the version.
