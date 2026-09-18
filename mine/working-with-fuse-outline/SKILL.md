---
name: working-with-fuse-outline
description: Use when reading, creating, or updating Fuse documentation in Outline, or before any other write to a page under the Fuse root.
---

# Working With Fuse Outline

**REQUIRED SUB-SKILL:** Use `working-with-outline`. Every rule there applies here unchanged, including draft-then-confirm on every write.

This skill adds one constraint: the Fuse root is the only Outline tree in scope.

## The Fuse Root

| | |
| --- | --- |
| Root page | `https://imarc.getoutline.com/doc/fuse-z4uA0cqqZ4` |
| Root `outline_fetch` ID | `844a248d-e497-4e2c-9edc-72c547048eeb` (`resource: "document"`) |

Treat `fuse-z4uA0cqqZ4` as the root of the Outline world. Read and write only that page and its descendants. Every other Outline page is out of scope: leave it unread, unsearched, unlisted, and unchanged.

New documentation is created as a child inside this tree. Before any write, name the target page and its parent, and confirm that parent resolves to the root or one of its descendants.

`/doc/fuse-z4uA0cqqZ4` is a URL slug, not an `outline_fetch` ID. Fetch the root by the ID above; fetch child pages by their own known IDs.

## When Outline Is Unavailable

Use repository evidence and continue without blocking.

Documentation is context. When it conflicts with active local code, CPA-Tech, Inkwell, configuration, dependency versions, or runtime resolution, trust the code, report the difference, and leave working code as it is.
