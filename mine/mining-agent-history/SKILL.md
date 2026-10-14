---
name: mining-agent-history
description: Mine Claude Code and Codex session history for repeated tasks and repeated asks, and propose skills or AGENTS.md/CLAUDE.md rules backed by evidence. Use when asked to review past agent sessions for skill candidates, to refine or prune AGENTS.md or CLAUDE.md from history, or to find what the agent keeps being told.
---

# Mining agent history

Find what keeps coming up in past Claude Code and Codex sessions, and propose skills and instruction rules for it. This workflow ends at a report: the user picks candidates and writes them afterwards.

Read history only through the script: raw session JSONL is mostly injected noise. Run it from this skill's directory (Python 3.9+, standard library only):

```
python3 scripts/history.py <subcommand> [--project [PATH] | --global] [--since YYYY-MM-DD] [--limit N]
```

| Subcommand | Output |
|---|---|
| `inventory` | existing skills and instruction files in scope |
| `stats` | repeated prompts, corrections, commands, skills, edited files; every row cites sessions as `agent:id tN` |
| `digest ID...` | compact transcript of chosen sessions; pass the `agent:id` part of a cite |
| `discover` | every session in scope with its resolved repo; use to debug scope |

History is read from `$CLAUDE_CONFIG_DIR` (default `~/.claude`) and `$CODEX_HOME` (default `~/.codex`). Override with `--claude-dir` and `--codex-dir`. Codex threads whose log files Codex has deleted are rebuilt from its SQLite history when present; `discover` shows their path as the `thread_history_N.sqlite` file.

Use the strongest model available. The input is small; the work is judgment.

## Workflow

1. **Scope.** Default is project mode: the current repo, including its worktrees. Use `--global` when the user asks about all projects or general habits.
2. **Read** `inventory`, then `stats`. Read every `warning:` line first. A warning means the log format may have changed and results may be incomplete; tell the user.
3. **Form hypotheses** from `stats`:
   - Repeated-prompt group: skill candidate.
   - Correction group: rule candidate.
   - Same command prefixes across sessions: skill candidate, possibly with a script.
4. **Verify** each hypothesis: run `digest` on at most 5 of its cited sessions. Keep it only when it repeats across sessions, not one session's back-and-forth.
5. **Apply thresholds.**
   - Project mode: at least 3 occurrences across at least 2 sessions.
   - Global mode: at least 2 repos and at least 3 sessions.
   - A single emphatic correction ("always", "never", "absolutely not") may be listed at low confidence.
6. **Route** each candidate:
   - Multi-step procedure with a clear trigger: skill.
   - Standing constraint, preference, or fact: instruction rule.
   - Already covered by `inventory` but still repeated: the existing item is not working. Propose a stronger rule, or a skill description that triggers.
   - Existing rule with no related evidence: prune candidate, low confidence only. Silence can mean the rule works.
   - Project mode: candidate with nothing project-specific: also list under "Promote to global?".
   - Global mode: keep only candidates that hold across projects.
7. **Rank** by sessions affected, then correction count, then turns spent per occurrence.
8. **Report** in chat using the format below. Done when every listed candidate cites sessions you read in a `digest`. Write the report to a file only when the user asks: quotes come from private transcripts.

## Report format

```
## Skills
### 1. [project skill] <proposed-name>
- Pattern: <one line>
- Evidence: 7 sessions - 2026-09-12 `claude:ab12cd34-5678` t3 "<quote>", ...
- Draft description: <what it does and when to use it>
- Overlap: none | extends `<existing-skill>`
- Confidence: high | med | low

## Rules (add | strengthen | prune)
### 1. [AGENTS.md - add] <rule text>
- Pattern / Evidence / Overlap / Confidence

## Promote to global?   (project mode only)
```

## After the report

To write a chosen candidate, prefer your agent's skill-authoring skill if one is installed. Otherwise follow [references/writing-guide.md](references/writing-guide.md).

Rule targets:
- Project: `AGENTS.md`, or `CLAUDE.md` when the repo only has that.
- Global: `$CLAUDE_CONFIG_DIR/CLAUDE.md` (default `~/.claude/CLAUDE.md`) and `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`), whichever exist.

## Limits

- Correction tagging is English-only and heuristic. Verify with `digest`.
- Session log formats are undocumented and change between versions. Heed `warning:` lines.
