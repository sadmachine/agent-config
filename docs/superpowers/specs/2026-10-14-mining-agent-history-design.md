# mining-agent-history — design

Date: 2026-10-14
Status: draft, awaiting review

## Purpose

Review the AI chat history (Claude Code and Codex sessions) for one repository,
or across all repositories, and propose:

- tasks repeated often enough to become a **skill** (project or global), and
- asks or corrections repeated often enough to become an **instruction rule**
  (project `AGENTS.md`/`CLAUDE.md`, or the global instruction files), including
  strengthening rules that are ignored and pruning rules that never matter.

The skill proposes, with evidence. It never writes skills or rules itself; the
user picks candidates and writes them in a follow-up step.

## Constraints

- **Portable.** Works on any machine with Claude Code and/or Codex history. No
  dependency on other skills, personal paths, or tools beyond Python 3.9+
  standard library. Agent-neutral `SKILL.md` (frontmatter `name` and
  `description` only, no agent-specific tool names).
- **Small context.** Raw sessions are ~99% noise (one measured 726 KB session
  held 2.1 KB of human prompts). The model never reads raw JSONL; scripts reduce
  and count, the model judges.
- **No secrets leak.** Transcripts contain credentials. Redaction happens in the
  parsers, before any output.

## Non-goals (v1)

- Writing or editing skills and instruction files.
- Embeddings, trained classifiers, or map-reduce over sub-models. Total human
  prompt volume across all history measured ~420 KB, which fits without them.
- Agents other than Claude Code and Codex.
- Caching. A full parse of 738 MB of history takes ~2 s.

## Layout

```
mine/mining-agent-history/
  SKILL.md                     workflow, thresholds, routing, report format
  scripts/history.py           single stdlib script, four subcommands
  references/writing-guide.md  minimal skill + instruction-rule writing guide
  tests/test_history.py        unittest
  tests/fixtures/claude/...    synthetic Claude Code sessions
  tests/fixtures/codex/...     synthetic Codex sessions
```

Linked into `skills/` by the existing `bin/link-mine`. The directory is
self-contained and can be copied or published as-is.

## Modes

| Mode | Input | Buckets | Acceptance threshold |
|------|-------|---------|----------------------|
| `--project [path]` (path defaults to cwd) | sessions mapped to that repo | project skill; project instruction rule (add / strengthen / prune); "promote to global?" tag | ≥3 occurrences across ≥2 sessions |
| `--global` | all sessions | global skill; global instruction rule | ≥2 repos and ≥3 sessions |

A single emphatic correction ("always", "never", "ABSOLUTELY NOT") is listed at
low confidence rather than dropped.

## Data model

Two parsers (Claude, Codex) emit one shared record stream. Everything after the
parsers is agent-agnostic.

```
Turn {agent, session, repo, cwd, branch, ts, index, kind, text}
kind ∈ prompt | reply | command | skill | edit | interrupt | denial | answer
```

`Session {agent, id, path, repo, cwd, branch, started, turns[], unknown_records, total_records}`

## Session discovery and locations

- Claude Code: `$CLAUDE_CONFIG_DIR/projects/*/*.jsonl`, falling back to
  `~/.claude/projects`. Top-level files only; subagent transcripts are skipped.
- Codex: `$CODEX_HOME/sessions/**/*.jsonl` and `$CODEX_HOME/archived_sessions/**/*.jsonl`,
  falling back to `~/.codex`. Newer Codex versions delete old rollout files and
  keep thread history in SQLite; see "Codex SQLite history" below.
- `--claude-dir` / `--codex-dir` override. A missing store is skipped with a
  note, so single-agent machines work.
- `--since YYYY-MM-DD` filters by session start.

## Parsing rules

### Claude Code

- Session cwd and branch: first record carrying `cwd` / `gitBranch`.
- Drop record types `attachment`, `system`, `file-history-snapshot`, `mode`,
  `permission-mode`, `cost-state`, `ai-title`, `last-prompt`, and any record
  with `isSidechain: true`. Drop assistant `thinking` blocks.
- Human prompt: `type == "user"` and `origin.kind == "human"`. When `origin` is
  absent (older versions), accept string content unless it starts with an
  injection prefix: `<local-command-`, `<command-name>`, `<command-message>`,
  `<task-notification>`, `<system-reminder>`, `Base directory for this skill:`,
  `Caveat:`.
- `<command-name>/x</command-name>` → `skill` turn (slash command `x`).
- `tool_use` name `Skill` → `skill` turn (`input.skill`).
- `tool_use` name `Bash` → `command` turn (`input.command`).
- `tool_use` names `Edit`, `Write`, `MultiEdit`, `NotebookEdit` → `edit` turn
  (`input.file_path`).
- `AskUserQuestion` tool result → `answer` turn.
- `[Request interrupted by user` → `interrupt` turn.
- Tool result rejected by the user (permission denial text) → `denial` turn.
- Assistant `text` blocks → `reply` turns (kept only for correction context).

### Codex

- Skip the whole session when `session_meta.payload.source` is an object
  containing `subagent` (guardian, thread_spawn).
- Session cwd and repo URL: `session_meta.payload.cwd` and
  `session_meta.payload.git.repository_url`.
- Human prompt: prefer `event_msg` with `payload.type == "user_message"`. If a
  session has none (newer versions), use `response_item` messages with
  `role == "user"`, dropping text that starts with `# AGENTS.md instructions`,
  `<environment_context>`, `<user_instructions>`, `<skills_instructions>`.
- `<send_user_message_question_reply>` → `answer` turn.
- Drop `role == "developer"`, `reasoning`, `token_count`, `turn_context`,
  `token_usage_record`, `world_state`.
- `function_call` / `custom_tool_call` running a shell command → `command`
  turn. A command reading a `SKILL.md` → `skill` turn (name = parent dir).
- `apply_patch` input → `edit` turns (`*** Add File:` / `*** Update File:` /
  `*** Delete File:` paths).
- Assistant `output_text` → `reply` turns.

### Codex SQLite history

Added 2026-10-14 after Codex deleted 393 of 472 rollout files on this machine
while keeping the threads in SQLite.

- Read only when both `$CODEX_HOME/state_N.sqlite` and
  `$CODEX_HOME/thread_history_N.sqlite` exist; use the highest `N` of each.
- Open read-only (`mode=ro`); if that fails, `mode=ro&immutable=1`, which skips
  writes not yet checkpointed (only affects sessions still running).
- `threads` gives id, cwd, `git_origin_url`, `git_branch`, `created_at`, and
  `source` (subagent when it is a JSON object with `subagent`).
- A thread is rebuilt only when its `rollout_path` no longer exists and no parsed
  rollout has its id, so nothing is counted twice.
- `thread_items` in `rollout_ordinal` order: `userMessage` text parts → same
  rules as Codex user text (answers, injections, `$skill`); `skill` parts →
  `skill` turn; `agentMessage.text` → `reply`; `commandExecution.command` →
  `command`; `fileChange.changes[].path` → `edit`.
- `thread_turns.status == "interrupted"` → `interrupt` turn after that turn.
- Any `sqlite3.Error` → one `warning:` note; JSONL results still stand.

### Shared

- **Redaction** on every emitted text: `sk-…`, `ghp_…`/`gho_…`/`github_pat_…`,
  `AKIA…`, `xox[abpr]-…`, JWTs (`eyJ…\.…\.…`), PEM blocks, and `KEY=value` /
  `"key": "value"` where the key contains `SECRET`, `TOKEN`, `PASSWORD`,
  `API_KEY`, or `PRIVATE`. Replaced with `[REDACTED]`.
- **Long pastes**: text over 1500 chars truncated to its head plus
  `[+N chars]`.
- **Command prefix**: split on `&&`, `||`, `;`, `|`; skip `cd`; strip leading
  env assignments and wrappers (`sudo`, `env`, `time`, `nohup`, `rtk`); keep the
  first two tokens (`git push`, `composer install`, `bin/hiraeth`).
- **Correction tag** on prompts matching (case-insensitive, English only):
  `^(no|nope|wrong|stop)\b`, `\bdon'?t\b`, `\binstead\b`, `\bagain\b`,
  `\bi (said|told you|asked)\b`, `\balways\b`, `\bnever\b`, `\bstill\b`,
  `\bthat'?s not\b`, `\bnot what\b`, plus any prompt directly following an
  `interrupt` or `denial`.
- **Format drift**: unknown record shapes are skipped and counted. More than
  20% unknown in a session prints a warning that the format may have changed.
  A malformed line never aborts the run.

## Repo resolution

Each session gets a repo key. Resolution is memoized per cwd.

1. Codex `git.repository_url`.
2. cwd exists: `git -C <cwd> remote get-url origin`; no remote: the parent of
   `git rev-parse --path-format=absolute --git-common-dir` as a path key.
3. Paseo, only if `~/.paseo/projects/workspaces.json` exists: match `cwd` to a
   workspace, follow `mainRepoRoot`, retry step 2.
4. Known worktree conventions: strip `/.worktrees/<name>` or
   `/.claude/worktrees/<name>` and retry step 2.
5. Unresolved: key is the cwd path. Listed in `discover` output, never dropped
   silently.

Remote URLs normalize to `host/owner/repo` (`git@host:o/r.git`,
`https://host/o/r`, `ssh://git@host/o/r.git` all equal).

`--project <path>` matches sessions whose repo key equals the target's key, or
whose cwd is under `<path>` (covers non-git directories).

## Subcommands

All output is Markdown or TSV on stdout, capped by `--limit N` (default 40
rows per table) so global mode stays small. Every row cites session ids.

- `discover --project <path> | --global [--since DATE]`
  TSV: agent, session id, path, repo key, cwd, branch, started, human turns.
  Footer: unresolved count, skipped subagent count, missing stores.
- `stats --project <path> | --global [--since DATE] [--limit N]`
  - Prompt groups: all human prompts, lowercased, paths and numbers stripped,
    grouped greedily by word-trigram Jaccard ≥ 0.5. Counted by distinct
    sessions; only groups spanning ≥2 sessions shown. Global mode adds repo
    count.
  - Correction-tagged prompts (truncated to 200 chars), grouped the same way.
  - Command prefixes, skills/slash commands, edited files: counted by distinct
    sessions.
- `digest <session-id>... [--project <path>]`
  Per session: header (agent, date, repo, branch), numbered prompts verbatim
  (redacted), correction tags, the 300-char reply preceding each correction,
  commands, skills, edits, interrupts, denials, answers. Session ids match by
  prefix.
- `inventory --project <path> | --global`
  - Skills (name, description truncated to 200 chars, source dir) from whichever
    exist: `$CLAUDE_CONFIG_DIR/skills`, `$CLAUDE_CONFIG_DIR/plugins/cache/**/skills`,
    `$CODEX_HOME/skills`, `~/.agents/skills`; project mode adds
    `<repo>/.claude/skills` and `<repo>/.agents/skills`.
  - Instruction files, full content: project `AGENTS.md` and/or `CLAUDE.md`;
    global `$CLAUDE_CONFIG_DIR/CLAUDE.md` and/or `$CODEX_HOME/AGENTS.md`.
    `@path` imports are listed, not inlined.

## Skill workflow (SKILL.md)

1. Choose scope: `--project` on the current repo by default; `--global` when
   asked.
2. Run `inventory` and `stats`. Read both.
3. Form hypotheses:
   - prompt group → skill candidate,
   - correction group → rule candidate,
   - command sequence repeated across sessions → skill (possibly with script).
4. Verify each with `digest` on at most 5 cited sessions. Drop candidates that
   are one session's back-and-forth rather than a repeat.
5. Route:
   - multi-step procedure with a clear trigger → skill,
   - standing constraint, preference, or fact → instruction rule,
   - repeat already covered by inventory → existing item is ineffective:
     strengthen the rule or fix the skill description so it triggers,
   - existing rule with zero related evidence → prune candidate, low
     confidence only (silence may mean the rule works).
6. Rank by sessions affected, then correction count, then turns spent per
   occurrence.
7. Print the report in chat and stop. Write to a file only if asked.

Model guidance: use the strongest model available; the work is judgment-heavy
and low-volume.

Hand-off: "To write a chosen candidate, prefer your agent's skill-authoring
skill if it has one; otherwise follow `references/writing-guide.md`."

## Report format

```
## Skills
### 1. [project skill] <proposed-name>
- Pattern: <one line>
- Evidence: 7 sessions — 2026-09-12 `ab12cd` t3 "<quote>", …
- Draft description: <trigger sentence>
- Overlap: none | extends `<existing-skill>`
- Confidence: high | med | low

## Rules (add | strengthen | prune)
### 1. [AGENTS.md · add] <rule text>
- Pattern / Evidence / Overlap / Confidence

## Promote to global?   (project mode only)
```

## references/writing-guide.md

Short and self-contained:

- Skill: directory with `SKILL.md`; frontmatter `name` (kebab-case) and
  `description` stating what it does and when to use it; body is imperative
  steps, no background; scripts for deterministic work.
- Instruction rule: one rule per bullet, imperative, with the reason when not
  obvious; place under an existing heading when one fits; prefer `AGENTS.md`
  in projects.

## Testing

Committed in `tests/`, run with `python3 -m unittest discover -s tests` from the
skill directory.

Synthetic fixtures:

- Claude session with human prompts, injected noise (attachment, system
  reminder, skill body, `/clear`, task notification), a sidechain record, a
  Bash command, an edit, an interrupt, and a planted secret.
- Older Claude session without `origin` fields.
- Codex session (newer format: `response_item` user messages with AGENTS.md
  and environment injections, developer messages, `apply_patch`, a question
  reply).
- Codex session with `event_msg` `user_message`.
- Codex subagent session (must be skipped).
- Session whose cwd no longer exists, resolved through a fixture
  `workspaces.json`.
- A malformed JSONL line.

Assertions: only human prompts survive filtering; subagent and sidechain
content excluded; secrets redacted; repo keys normalized and grouped; deleted
worktree resolved; malformed line counted, not fatal; correction tags applied;
prompt groups counted by distinct sessions.

## Verification

1. Unit tests pass.
2. Real run: `stats --project ~/.agents` and `stats --global`. Check no injected
   text appears as prompts, no secrets appear, output stays within limits, and
   runtime stays under 10 s.
3. Run the full skill once in project mode and confirm the report cites real
   sessions.

## Risks

- Both log formats are undocumented and change between versions. Mitigated by
  tolerant parsing, drift warnings, and fixtures per known shape.
- Correction tagging is English-only and heuristic; the model verifies every
  candidate against digests before reporting.
- Report quotes contain user text; it stays in chat unless the user asks for a
  file.
