---
name: working-across-fuse
description: Use when a task spans more than one Fuse site, or targets a Fuse site outside the current working directory — auditing which sites need a CPA-Tech or Inkwell change, applying the same fix across sites, or comparing config, templates, or data between sites.
---

# Working Across Fuse

Locate the Fuse sites, then run **targeted** work against each one. Single-site work belongs to `working-with-fuse`.

## Listing The Sites

`~/.config/fuse/config` is a `key=value` file. `sites_root` names the directory holding the sites, one site per immediate child; `sites_file` names the curated site list, relative to `sites_root`, one site directory name per line. The list is the authority — use it rather than scanning:

```bash
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/fuse/config"
FUSE_SITES_DIRECTORY="$(grep -m1 '^sites_root=' "$CFG" | cut -d= -f2-)"
sed "s|^|$FUSE_SITES_DIRECTORY/|" "$FUSE_SITES_DIRECTORY/$(grep -m1 '^sites_file=' "$CFG" | cut -d= -f2-)"
```

If `sites_file` is missing, fall back to the criteria scan below. If `sites_root` is missing, ask the user for the path, verify the scan lists at least one site there (if none do, report that and ask again), then ask whether to save it. On yes, append `sites_root=<path>` to the config file. Use the path for this session either way.

## Identifying A Fuse Site

A directory is a Fuse site when both hold: `bin/hiraeth` exists, and `composer.json` requires `imarc/cpa-tech`. `working-with-fuse` owns this criteria and the rules for editing inside a site; consult it before changing any site's code.

Scan, when the curated list is unavailable or a site's membership is in question:

```bash
for d in "$FUSE_SITES_DIRECTORY"/.worktrees/*/release; do
  [ -f "$d/bin/hiraeth" ] && grep -q '"imarc/cpa-tech"' "$d/composer.json" 2>/dev/null && basename "$(dirname "$d")"
done
```

## Worktrees

Each site keeps its release worktree at `$FUSE_SITES_DIRECTORY/.worktrees/<site>/release`, tracking `origin/release`. Read and probe there — `$FUSE_SITES_DIRECTORY/<site>` points at the ops clone on another branch, so it answers the wrong question.

<EXTREMELY-IMPORTANT>
The release worktree is read-only. Every change belongs on its own worktree, branched off remote `release`.
</EXTREMELY-IMPORTANT>

Once a probe marks a site as affected, create that worktree. `working-with-fuse` owns the branch name (Asana `Branch Name`, else `<update|bugfix|feature>/<number>-<two-to-three-word-summary>`):

```bash
WT="$FUSE_SITES_DIRECTORY/.worktrees/<site>"
git -C "$WT/release" fetch origin release
git -C "$WT/release" worktree add "$WT/<branch>" -b <branch> origin/release
git -C "$WT/<branch>" push -u origin <branch>
```

Upstream must carry the same name as the local branch. Work, commit, and verify inside `$WT/<branch>`.

## Targeted Work Only

<EXTREMELY-IMPORTANT>
Never read a site's codebase broadly. Every site is a full application; a handful of them exhausts the context window and the run dies mid-audit.
</EXTREMELY-IMPORTANT>

Work from a narrow question, asked the same way at every site:

1. **Name the signal first** — the symbol, config key, template path, or dependency constraint that decides whether a site is affected.
2. **Probe each site's release worktree**, one command, output trimmed to the answer: `grep -rl`, `grep -c`, `jq` on `composer.json`, a file-existence test. Collect the results into one affected/unaffected list before opening anything.
3. **Open files only at the sites the probe named**, and only the files it named.
4. **Apply the change on a per-site branch worktree**, following `working-with-fuse` for layer ownership and verification. Sites are separate repos: separate branches, diffs, and commits.

Report the full list — affected, unaffected, and any site the probe could not answer for.
