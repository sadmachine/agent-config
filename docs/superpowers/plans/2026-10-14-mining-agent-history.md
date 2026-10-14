# mining-agent-history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A portable agent skill that mines Claude Code and Codex session history for one repo or all repos and proposes skills and instruction rules with evidence.

**Architecture:** One standard-library Python script (`scripts/history.py`) parses both log formats into a shared `Session`/`Turn` model, drops injected noise, redacts secrets, resolves each session to a repo key, and exposes four subcommands (`discover`, `stats`, `digest`, `inventory`). `SKILL.md` drives the model: read `inventory` + `stats`, verify hypotheses with `digest`, route, report, stop.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `json`, `re`, `shlex`, `subprocess`, `glob`, `dataclasses`), `unittest`, git CLI.

**Spec:** `docs/superpowers/specs/2026-10-14-mining-agent-history-design.md`

## Global Constraints

- Python 3.9+ standard library only. No third-party imports. No 3.10+ syntax (`match`, `X | Y` annotations, parenthesized context managers).
- One script: `mine/mining-agent-history/scripts/history.py`. Tests: `mine/mining-agent-history/tests/test_history.py`, run from the skill directory with `python3 -m unittest discover -s tests -v`.
- `SKILL.md` frontmatter has only `name` and `description`. `SKILL.md` and `references/` contain no agent-specific tool names and no personal tooling (`rtk`, caveman, Paseo).
- No hardcoded personal paths. Defaults: `$CLAUDE_CONFIG_DIR` else `~/.claude`; `$CODEX_HOME` else `~/.codex`; Paseo file `~/.paseo/projects/workspaces.json` read only if it exists.
- Constants: `PASTE_LIMIT = 1500`, `REPLY_CONTEXT = 300`, `QUOTE_LIMIT = 200`, `SIMILARITY = 0.5`, `DRIFT_RATIO = 0.2`, `--limit` default `40`.
- Redaction runs inside the parsers, before any text reaches output.
- A malformed line or unexpected record shape never aborts a run; it is counted.
- Script output is ASCII apart from user text.
- Commits: plain-sentence subject (repo style, e.g. "Derive skills/ from mine/ and the lockfile"), body optional, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage named files only. Confirm with the user before committing unless they approved per-task commits on branch `mining-agent-history`.

## Review Focus

1. Very long pasted prompt (logs, stack traces) → kept, clipped to 1500 chars with a `[+N chars]` marker. Test: Task 1 `test_clean_clips_long_paste`.
2. Prompt or description containing `|` or newlines inside a Markdown table → one escaped line, table intact. Test: Task 1 `test_cell_is_one_escaped_line`.
3. Session file with invalid UTF-8 bytes and malformed JSON lines → counted, drift warning printed, no crash. Test: Task 5 `test_drift_warning_without_crash`.
4. Codex file containing a second `session_meta` (resumed or forked thread) → the first one defines the session. Test: Task 3 `test_metadata` (fixture's last line).
5. `--project` given a subdirectory with a trailing slash → resolves to the repo root and still matches. Test: Task 5 `test_project_subdirectory_resolves_to_repo`.

## Deviations from the spec (refinements found while probing real logs)

- Drift = malformed lines + `user`/`assistant` (Claude) or `session_meta`/`event_msg`/`response_item` (Codex) records with an unexpected shape, plus a store-level warning when no prompts are recognized at all. Metadata record types (`atis-latch`, `agent-name`, …) change too often to whitelist, so other types are ignored silently.
- Codex extras seen in real logs: `<turn_aborted>` → `interrupt`; `Answers to your questions:` → `answer`; leading `$skill` in a prompt → `skill`.
- Built-in slash commands (`/clear`, `/exit`, …) are not counted as skills.
- Unresolvable worktree paths fall back to the stripped repo path, so they group with the main checkout.
- `inventory --project` also lists global instruction files, so a repeated ask already covered globally is caught as "existing rule not working".
- `Turn` holds `index, kind, text, correction`; agent, repo, cwd, branch, and time live on its `Session`.
- Claude continued sessions copy their parent's records (same message `uuid`), which double-counted repeats. The loader shares a set of seen uuids across Claude files.
- Claude prompts with `promptSource: "sdk"` (scripts calling `claude -p`) are not human, even without an `origin` field.
- Command prefixes drop heredoc bodies, shell syntax (`for`, `do`, `done`, …), and option-like second words, so `head -20` counts as `head` and `git -C <path> status` as `git status`. `shlex` is not used: it was the slowest step and quoted tokens are rejected anyway.
- Known limit: Codex forked threads may repeat parent history (a few percent of files here); Codex records have no message ids to deduplicate them.

These came from running the assembled plan code against this machine's history (542 sessions): all tests passed, no injected text or secrets appeared in `stats` output, but global `stats` took 14 s and showed the issues above.

## File Structure

```
mine/mining-agent-history/
  SKILL.md                     workflow, thresholds, routing, report format (Task 9)
  references/writing-guide.md  standalone writing guide (Task 9)
  scripts/history.py           all mechanics (Tasks 1-8)
  tests/test_history.py        unittest suite (Tasks 1-8)
  tests/fixtures/claude/projects/-fixture-app/*.jsonl          (Task 2)
  tests/fixtures/codex/sessions/2026/MM/DD/rollout-*.jsonl     (Task 3)
```

`history.py` section order: constants and text helpers → model → Claude parser → Codex parser → repo resolution → loading and `discover` → `stats` → `digest` → `inventory` → `main`. Later tasks insert their functions above `def main(`.

All paths below are relative to `~/.agents`. Run test commands from `~/.agents/mine/mining-agent-history`.

---

### Task 1: Script skeleton and text helpers

**Files:**
- Create: `mine/mining-agent-history/scripts/history.py`
- Create: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `redact(text) -> str`, `clip(text, limit) -> str`, `clean(text) -> str`, `cell(text) -> str`, `command_prefixes(command) -> list[str]`, `is_correction(text) -> bool`; constants `PASTE_LIMIT`, `REPLY_CONTEXT`, `QUOTE_LIMIT`, `SIMILARITY`, `MIN_SHINGLES`, `DRIFT_RATIO`, `CITES_PER_ROW`.

- [ ] **Step 1: Write the failing tests**

Create `mine/mining-agent-history/tests/test_history.py`:

```python
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import history  # noqa: E402


class TextHelpers(unittest.TestCase):
    def test_redacts_token_shapes(self):
        text = "key sk-abcdefghijklmnop1234 and ghp_" + "a" * 36 + " and AKIAABCDEFGHIJKLMNOP"
        out = history.redact(text)
        self.assertNotIn("sk-abc", out)
        self.assertNotIn("ghp_a", out)
        self.assertNotIn("AKIA", out)
        self.assertEqual(out.count("[REDACTED]"), 3)

    def test_redacts_secret_assignments(self):
        self.assertEqual(history.redact("DB_PASSWORD=hunter2 ok"), "DB_PASSWORD=[REDACTED] ok")
        self.assertEqual(history.redact('{"api_key": "abc123"}'), '{"api_key": "[REDACTED]"}')

    def test_leaves_plain_text(self):
        self.assertEqual(history.redact("push the branch to dev"), "push the branch to dev")

    def test_clip_marks_truncation(self):
        self.assertEqual(history.clip("abcdef", 4), "abcd [+2 chars]")
        self.assertEqual(history.clip("  abc  ", 4), "abc")

    def test_clean_clips_long_paste(self):
        self.assertTrue(history.clean("a" * 1600).endswith("[+100 chars]"))

    def test_cell_is_one_escaped_line(self):
        self.assertEqual(history.cell("a | b\nc"), "a \\| b c")

    def test_command_prefixes(self):
        command = "cd repo && FOO=1 rtk git push origin main | tee log; npm test"
        self.assertEqual(history.command_prefixes(command), ["git push", "tee log", "npm test"])

    def test_command_prefixes_skip_shell_syntax(self):
        command = "for f in *.md; do echo $f; done\ncat > x <<'EOF'\nline one\nEOF\ngit -C /repo status\nhead -20 file\n# note"
        self.assertEqual(history.command_prefixes(command), ["echo", "cat", "git status", "head"])

    def test_correction_tags(self):
        for text in ["No, use the release branch", "don't touch vendor", "I told you to use pnpm", "that’s not right"]:
            self.assertTrue(history.is_correction(text), text)
        for text in ["Add a migration for the users table", "Nothing else needed"]:
            self.assertFalse(history.is_correction(text), text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'`

- [ ] **Step 3: Write the implementation**

Create `mine/mining-agent-history/scripts/history.py`:

```python
#!/usr/bin/env python3
"""Mine Claude Code and Codex session history for repeated tasks and asks.

Subcommands:
  discover   list sessions in scope with their resolved repo
  stats      repeated prompts, corrections, commands, skills, edited files
  digest     compact transcript of chosen sessions
  inventory  existing skills and instruction files

Python 3.9+ standard library only. Never prints raw transcripts: injected
noise is dropped and secrets are redacted while parsing.
"""
import argparse
import collections
import dataclasses
import glob
import json
import os
import re
import subprocess
import sys

PASTE_LIMIT = 1500  # chars kept from one message
REPLY_CONTEXT = 300  # chars of the reply shown before a correction
QUOTE_LIMIT = 200  # chars per table cell
SIMILARITY = 0.5  # word-trigram Jaccard needed to group two prompts
MIN_SHINGLES = 2  # shorter prompts ("yes", "continue") are not grouped
DRIFT_RATIO = 0.2  # unrecognized share of records that triggers a warning
CITES_PER_ROW = 6

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
]
SECRET_ASSIGNMENT = re.compile(  # starts at a word boundary: a leading optional quote made this 4x slower
    r"""(?i)\b([\w.-]*(?:secret|token|password|api_?key|private)[\w.-]*["']?\s*[:=]\s*)(["']?)[^\s"',;]+(\2)"""
)
COMMAND_SPLIT = re.compile(r"&&|\|\||[;|\n]")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PROGRAM = re.compile(r"^[\w./~+-]+$")
SUBCOMMAND = re.compile(r"^[a-z][a-z0-9-]*$")
WRAPPERS = {"sudo", "env", "time", "nohup", "rtk", "do", "then", "else", "if", "elif", "while", "until", "!"}
SHELL_SYNTAX = {"cd", "for", "done", "fi", "esac", "case"}
CORRECTION = re.compile(
    r"(?i)^(?:no|nope|wrong|stop)\b|\bdon['’]?t\b|\binstead\b|\bagain\b|\bi (?:said|told you|asked)\b"
    r"|\balways\b|\bnever\b|\bstill\b|\bthat['’]?s not\b|\bnot what\b"
)


def redact(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]\3", text)


def clip(text, limit):
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]} [+{len(text) - limit} chars]"


def clean(text):
    return clip(redact(text), PASTE_LIMIT)


def cell(text):
    """One Markdown table cell: clipped, single line, pipes escaped."""
    return " ".join(clip(text, QUOTE_LIMIT).split()).replace("|", "\\|")


def command_prefixes(command):
    """Program plus subcommand of each simple command; skips heredoc bodies, shell syntax, and wrappers."""
    prefixes = []
    for segment in COMMAND_SPLIT.split(HEREDOC.sub("", command)):
        words = segment.split()
        while words and (words[0] in WRAPPERS or ENV_ASSIGNMENT.match(words[0])):
            words = words[1:]
        if not words or words[0] in SHELL_SYNTAX or words[0].startswith("-") or not PROGRAM.match(words[0]):
            continue
        rest = words[1:]
        if words[0] == "git" and rest[:1] == ["-C"]:
            rest = rest[2:]
        prefixes.append(f"{words[0]} {rest[0]}" if rest and SUBCOMMAND.match(rest[0]) else words[0])
    return prefixes


def is_correction(text):
    return bool(CORRECTION.search(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 9 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Add text helpers for mining-agent-history" -m "Redaction, clipping, table cells, command prefixes, and correction tagging." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Session model and Claude Code parser

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (append after `is_correction`)
- Create: `mine/mining-agent-history/tests/fixtures/claude/projects/-fixture-app/c1a0de00-0001-4000-8000-000000000000.jsonl`
- Create: `mine/mining-agent-history/tests/fixtures/claude/projects/-fixture-app/c1a0de00-0002-4000-8000-000000000000.jsonl`
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `clean`, `clip`, `is_correction`, `QUOTE_LIMIT` (Task 1).
- Produces:
  - `Turn(index: int, kind: str, text: str, correction: bool = False)`; `kind` ∈ `prompt | reply | command | skill | edit | interrupt | denial | answer`.
  - `Session(agent, id, path, cwd="", branch="", started="", git_url="", repo="", subagent=False, total=0, unknown=0, turns=[])` with `.short -> "agent:id[:13]"`, `.add(kind, text)` (ignores empty text), `.prompts() -> list[Turn]`.
  - `read_jsonl(path, session)` generator of dict records; counts `session.total` and `session.unknown`.
  - `tag_corrections(session)`; `add_command(session, command)`; `parse_claude(path, seen=None) -> Session` (`seen` is a shared set of message uuids; records already seen are skipped).

- [ ] **Step 1: Create the Claude fixtures**

`tests/fixtures/claude/projects/-fixture-app/c1a0de00-0001-4000-8000-000000000000.jsonl` (exactly these 18 lines; line 16 is a scripted `claude -p` prompt, line 17 is deliberately broken):

```jsonl
{"type":"file-history-snapshot","messageId":"m0","snapshot":{}}
{"type":"attachment","cwd":"/fixture/app","gitBranch":"main","timestamp":"2026-09-01T10:00:00.000Z","attachment":{"type":"skill_listing","content":"many skills"}}
{"type":"user","cwd":"/fixture/app","gitBranch":"main","timestamp":"2026-09-01T10:00:01.000Z","origin":{"kind":"human"},"message":{"role":"user","content":"Merge the feature branch into dev and push dev to origin"}}
{"type":"user","isMeta":true,"message":{"role":"user","content":[{"type":"text","text":"Base directory for this skill: /x/skills/foo\n\n# Foo"}]}}
{"type":"user","message":{"role":"user","content":"<system-reminder>be terse</system-reminder>"}}
{"type":"user","message":{"role":"user","content":"<command-name>/clear</command-name>\n<command-message>clear</command-message>"}}
{"type":"user","message":{"role":"user","content":"<command-name>/fuse-dev-push</command-name>"}}
{"type":"user","origin":{"kind":"task-notification"},"message":{"role":"user","content":"<task-notification>done</task-notification>"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"secret plan"},{"type":"text","text":"I will run the deploy script now."},{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"rtk git push origin dev"}},{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"/fixture/app/src/app.py"}},{"type":"tool_use","id":"t3","name":"Skill","input":{"skill":"working-with-git"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"The user doesn't want to proceed with this tool use. The tool use was rejected."}]}}
{"type":"user","origin":{"kind":"human"},"message":{"role":"user","content":"Use the release branch, here is the key sk-abcdefghijklmnop1234"}}
{"type":"user","isSidechain":true,"origin":{"kind":"human"},"message":{"role":"user","content":"SIDECHAIN prompt should be ignored"}}
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"[Request interrupted by user]"}]}}
{"type":"user","origin":{"kind":"human"},"message":{"role":"user","content":"No, don't touch the vendor directory again"}}
{"type":"user","toolUseResult":{"questions":[],"answers":{"Run tests?":"Yes"}},"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t4","content":"Your questions have been answered"}]}}
{"type":"user","entrypoint":"sdk-cli","promptSource":"sdk","message":{"role":"user","content":"Compress this markdown file"}}
{"type": "user", BROKEN
{"type":"assistant","message":{"role":"assistant","content":42}}
```

`tests/fixtures/claude/projects/-fixture-app/c1a0de00-0002-4000-8000-000000000000.jsonl` (older format, no `origin`):

```jsonl
{"type":"user","cwd":"/fixture/app/.worktrees/feature-x","gitBranch":"feature-x","timestamp":"2026-09-02T09:00:00.000Z","message":{"role":"user","content":"Merge the feature branch into dev and push dev to origin please"}}
{"type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: ignore</local-command-caveat>"}}
{"type":"user","message":{"role":"user","content":"[Image: original 100x100]"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Merged and pushed."}]}}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
FIXTURES = os.path.join(HERE, "fixtures")
CLAUDE_DIR = os.path.join(FIXTURES, "claude")
CLAUDE_NEW = os.path.join(CLAUDE_DIR, "projects", "-fixture-app", "c1a0de00-0001-4000-8000-000000000000.jsonl")
CLAUDE_OLD = os.path.join(CLAUDE_DIR, "projects", "-fixture-app", "c1a0de00-0002-4000-8000-000000000000.jsonl")


def texts(session, kind):
    return [turn.text for turn in session.turns if turn.kind == kind]


class ClaudeParser(unittest.TestCase):
    def setUp(self):
        self.session = history.parse_claude(CLAUDE_NEW)

    def test_metadata(self):
        s = self.session
        self.assertEqual((s.agent, s.id, s.cwd, s.branch, s.started[:10]),
                         ("claude", "c1a0de00-0001-4000-8000-000000000000", "/fixture/app", "main", "2026-09-01"))
        self.assertEqual(s.short, "claude:c1a0de00-0001")

    def test_only_human_prompts_survive(self):
        prompts = texts(self.session, "prompt")
        self.assertEqual(len(prompts), 3)
        self.assertEqual(prompts[0], "Merge the feature branch into dev and push dev to origin")
        joined = " ".join(prompts)
        for noise in ("Base directory", "system-reminder", "task-notification", "SIDECHAIN", "command-name",
                      "Compress this markdown"):
            self.assertNotIn(noise, joined)

    def test_secrets_redacted(self):
        prompt = texts(self.session, "prompt")[1]
        self.assertIn("[REDACTED]", prompt)
        self.assertNotIn("sk-abc", prompt)

    def test_tools_skills_and_events(self):
        s = self.session
        self.assertEqual(texts(s, "command"), ["rtk git push origin dev"])
        self.assertEqual(texts(s, "edit"), ["/fixture/app/src/app.py"])
        self.assertEqual(texts(s, "skill"), ["/fuse-dev-push", "working-with-git"])
        self.assertEqual(len(texts(s, "denial")), 1)
        self.assertEqual(texts(s, "interrupt"), ["[Request interrupted by user]"])
        self.assertEqual(texts(s, "answer"), ["Run tests? = Yes"])
        self.assertNotIn("secret plan", " ".join(texts(s, "reply")))

    def test_corrections(self):
        self.assertEqual([turn.correction for turn in self.session.prompts()], [False, True, True])

    def test_malformed_and_bad_shapes_counted(self):
        self.assertEqual((self.session.total, self.session.unknown), (18, 2))

    def test_skill_reads_ignore_shell_variables(self):
        session = history.Session("claude", "x", "p")
        history.add_command(session, 'for d in */; do cat "$d/SKILL.md"; done')
        self.assertEqual(texts(session, "skill"), [])

    def test_older_format_without_origin(self):
        old = history.parse_claude(CLAUDE_OLD)
        self.assertEqual(texts(old, "prompt"), ["Merge the feature branch into dev and push dev to origin please"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `ClaudeParser` tests FAIL with `AttributeError: module 'history' has no attribute 'parse_claude'`; `TextHelpers` still PASS.

- [ ] **Step 4: Write the implementation**

Append to `scripts/history.py`:

```python
@dataclasses.dataclass
class Turn:
    index: int
    kind: str  # prompt | reply | command | skill | edit | interrupt | denial | answer
    text: str
    correction: bool = False


@dataclasses.dataclass
class Session:
    agent: str
    id: str
    path: str
    cwd: str = ""
    branch: str = ""
    started: str = ""
    git_url: str = ""
    repo: str = ""
    subagent: bool = False
    total: int = 0  # non-blank lines
    unknown: int = 0  # malformed lines and core records with an unexpected shape
    turns: list = dataclasses.field(default_factory=list)

    @property
    def short(self):
        return f"{self.agent}:{self.id[:13]}"

    def add(self, kind, text):
        if text:
            self.turns.append(Turn(len(self.turns), kind, text))

    def prompts(self):
        return [turn for turn in self.turns if turn.kind == "prompt"]


def read_jsonl(path, session):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            session.total += 1
            try:
                record = json.loads(line)
            except ValueError:
                session.unknown += 1
                continue
            if isinstance(record, dict):
                yield record
            else:
                session.unknown += 1


def tag_corrections(session):
    """Tag prompts that look like corrections or follow an interrupt or denial."""
    previous = ""
    for turn in session.turns:
        if turn.kind == "prompt":
            turn.correction = is_correction(turn.text) or previous in ("interrupt", "denial")
        if turn.kind != "reply":
            previous = turn.kind


SKILL_FILE = re.compile(r"(?<![$\w.-])([A-Za-z0-9][\w.-]+)/SKILL\.md\b")


def add_command(session, command):
    if command:
        session.add("command", clean(command))
        for name in SKILL_FILE.findall(command):
            session.add("skill", name)


# Claude Code: $CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session>.jsonl
SLASH_COMMAND = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
BUILTIN_COMMANDS = {
    "clear", "compact", "config", "context", "cost", "doctor", "exit", "fast", "help", "hooks", "ide",
    "login", "logout", "mcp", "model", "permissions", "plugin", "resume", "rewind", "status", "statusline", "usage",
}
CLAUDE_INJECTED = ("<", "[", "Base directory for this skill:")
USER_DENIAL = "The user doesn't want to proceed"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def parse_claude(path, seen=None):
    """seen: message uuids already parsed. A continued session copies its parent's records."""
    seen = set() if seen is None else seen
    session = Session("claude", os.path.splitext(os.path.basename(path))[0], path)
    for record in read_jsonl(path, session):
        session.cwd = session.cwd or record.get("cwd") or ""
        session.branch = session.branch or record.get("gitBranch") or ""
        session.started = session.started or record.get("timestamp") or ""
        uuid = record.get("uuid")
        if uuid in seen:
            continue
        if uuid:
            seen.add(uuid)
        kind = record.get("type")
        if kind not in ("user", "assistant") or record.get("isSidechain"):
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            session.unknown += 1
        elif kind == "user":
            claude_user(session, record, content)
        else:
            claude_assistant(session, content)
    tag_corrections(session)
    return session


def claude_user(session, record, content):
    result = record.get("toolUseResult")
    if isinstance(result, dict) and isinstance(result.get("answers"), dict):
        session.add("answer", clean("; ".join(f"{q} = {a}" for q, a in result["answers"].items())))
        return
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool_result":
            body = tool_result_text(part.get("content"))
            if body.startswith(USER_DENIAL):
                session.add("denial", clip(body, QUOTE_LIMIT))
            continue
        text = (part.get("text") or "").strip() if part.get("type") == "text" else ""
        command = SLASH_COMMAND.search(text)
        if command:
            if command.group(1) not in BUILTIN_COMMANDS:
                session.add("skill", "/" + command.group(1))
        elif text.startswith("[Request interrupted by user"):
            session.add("interrupt", text)
        elif claude_is_human(record, text):
            session.add("prompt", clean(text))


def claude_is_human(record, text):
    origin = record.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    if "promptSource" in record:  # "sdk" marks prompts sent by scripts (claude -p)
        return record["promptSource"] == "typed"
    # Older logs have neither: drop meta records and injected wrappers.
    return bool(text) and not record.get("isMeta") and not text.startswith(CLAUDE_INJECTED)


def tool_result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text") or "" for part in content if isinstance(part, dict))
    return ""


def claude_assistant(session, content):
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            session.add("reply", clean(part.get("text") or ""))
        elif part.get("type") == "tool_use" and isinstance(part.get("input"), dict):
            name, args = part.get("name"), part["input"]
            if name == "Bash":
                add_command(session, args.get("command") or "")
            elif name == "Skill":
                session.add("skill", args.get("skill") or "")
            elif name in EDIT_TOOLS:
                session.add("edit", args.get("file_path") or args.get("notebook_path") or "")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 17 tests, all PASS

- [ ] **Step 6: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests
git commit -m "Parse Claude Code sessions into human prompts and tool events" -m "Drops sidechains, skill bodies, reminders, and other injected user messages; redacts secrets; tags corrections." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Codex parser

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (append after `claude_assistant`)
- Create: `mine/mining-agent-history/tests/fixtures/codex/sessions/2026/09/03/rollout-2026-09-03T10-00-00-0199aaaa-0001-7000-8000-000000000000.jsonl`
- Create: `mine/mining-agent-history/tests/fixtures/codex/sessions/2026/01/05/rollout-2026-01-05T09-00-00-0188bbbb-0002-7000-8000-000000000000.jsonl`
- Create: `mine/mining-agent-history/tests/fixtures/codex/sessions/2026/09/04/rollout-2026-09-04T08-00-00-0199cccc-0003-7000-8000-000000000000.jsonl`
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `Session`, `read_jsonl`, `tag_corrections`, `add_command`, `clean` (Tasks 1-2).
- Produces: `parse_codex(path) -> Session` (sets `id`, `cwd`, `started`, `git_url`, `branch`, `subagent` from the first `session_meta`).

- [ ] **Step 1: Create the Codex fixtures**

`.../2026/09/03/rollout-2026-09-03T10-00-00-0199aaaa-0001-7000-8000-000000000000.jsonl` (newer format; last line is a second `session_meta` that must be ignored):

```jsonl
{"timestamp":"2026-09-03T10:00:00.000Z","type":"session_meta","payload":{"id":"0199aaaa-0001-7000-8000-000000000000","timestamp":"2026-09-03T10:00:00.000Z","cwd":"/fixture/gone-worktree","source":"cli","git":{"repository_url":"git@github.com:Acme/App.git","branch":"feature-y"}}}
{"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"<permissions>developer noise</permissions>"}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"# AGENTS.md instructions for /fixture\n\nbe nice"},{"type":"input_text","text":"<environment_context>\n  <cwd>/fixture</cwd>\n</environment_context>"}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"$handoff merge the feature branch into dev and push dev to origin"}]}}
{"type":"response_item","payload":{"type":"reasoning","summary":[]}}
{"type":"response_item","payload":{"type":"custom_tool_call","name":"exec","input":"const r = await tools.exec_command({\"cmd\":\"rtk git push origin dev\",\"workdir\":\"/fixture\"});"}}
{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"sed -n 1,80p /x/skills/working-with-git/SKILL.md\"}"}}
{"type":"response_item","payload":{"type":"custom_tool_call","name":"apply_patch","input":"*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** Add File: src/new.py\n+x\n*** End Patch"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Pushed dev."}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<turn_aborted>\nThe previous turn was interrupted on purpose.\n</turn_aborted>"}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"use the release branch instead"}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<send_user_message_question_reply> [{\"answer\":\"Yes\",\"question\":\"Add tests?\"}]"}]}}
{"type":"event_msg","payload":{"type":"token_count","info":null}}
{"type":"response_item","payload":{"type":"function_call","name":"shell","arguments":"{\"command\":[\"bash\",\"-lc\",\"npm test\"]}"}}
{"type":"session_meta","payload":{"id":"ffffffff-0000-7000-8000-000000000000","timestamp":"2026-09-04T00:00:00.000Z","cwd":"/other"}}
```

`.../2026/01/05/rollout-2026-01-05T09-00-00-0188bbbb-0002-7000-8000-000000000000.jsonl` (older format with `event_msg` `user_message`):

```jsonl
{"timestamp":"2026-01-05T09:00:00.000Z","type":"session_meta","payload":{"id":"0188bbbb-0002-7000-8000-000000000000","timestamp":"2026-01-05T09:00:00.000Z","cwd":"/fixture/app","source":"vscode"}}
{"type":"event_msg","payload":{"type":"user_message","message":"Add a migration for the orders table"}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Add a migration for the orders table"}]}}
```

`.../2026/09/04/rollout-2026-09-04T08-00-00-0199cccc-0003-7000-8000-000000000000.jsonl` (subagent):

```jsonl
{"timestamp":"2026-09-04T08:00:00.000Z","type":"session_meta","payload":{"id":"0199cccc-0003-7000-8000-000000000000","timestamp":"2026-09-04T08:00:00.000Z","cwd":"/fixture/app","source":{"subagent":{"other":"guardian"}}}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Assess the exact planned action"}]}}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
CODEX_DIR = os.path.join(FIXTURES, "codex")
CODEX_SESSIONS = os.path.join(CODEX_DIR, "sessions", "2026")
CODEX_NEW = os.path.join(CODEX_SESSIONS, "09", "03", "rollout-2026-09-03T10-00-00-0199aaaa-0001-7000-8000-000000000000.jsonl")
CODEX_EVENTS = os.path.join(CODEX_SESSIONS, "01", "05", "rollout-2026-01-05T09-00-00-0188bbbb-0002-7000-8000-000000000000.jsonl")
CODEX_SUBAGENT = os.path.join(CODEX_SESSIONS, "09", "04", "rollout-2026-09-04T08-00-00-0199cccc-0003-7000-8000-000000000000.jsonl")


class CodexParser(unittest.TestCase):
    def setUp(self):
        self.session = history.parse_codex(CODEX_NEW)

    def test_metadata(self):
        s = self.session
        self.assertEqual(s.id, "0199aaaa-0001-7000-8000-000000000000")
        self.assertEqual((s.cwd, s.branch, s.git_url, s.started[:10]),
                         ("/fixture/gone-worktree", "feature-y", "git@github.com:Acme/App.git", "2026-09-03"))
        self.assertFalse(s.subagent)

    def test_prompts_exclude_injections(self):
        self.assertEqual(texts(self.session, "prompt"),
                         ["$handoff merge the feature branch into dev and push dev to origin",
                          "use the release branch instead"])

    def test_commands_skills_edits(self):
        s = self.session
        self.assertEqual(texts(s, "command"), ["rtk git push origin dev",
                                               "sed -n 1,80p /x/skills/working-with-git/SKILL.md", "npm test"])
        self.assertEqual(texts(s, "skill"), ["$handoff", "working-with-git"])
        self.assertEqual(texts(s, "edit"), ["src/app.py", "src/new.py"])
        self.assertEqual(texts(s, "reply"), ["Pushed dev."])

    def test_events_and_corrections(self):
        s = self.session
        self.assertEqual(texts(s, "interrupt"), ["[turn aborted]"])
        self.assertEqual(len(texts(s, "answer")), 1)
        self.assertEqual([turn.correction for turn in s.prompts()], [False, True])
        self.assertEqual(s.unknown, 0)

    def test_event_msg_prompts_preferred(self):
        self.assertEqual(texts(history.parse_codex(CODEX_EVENTS), "prompt"), ["Add a migration for the orders table"])

    def test_subagent_flagged(self):
        self.assertTrue(history.parse_codex(CODEX_SUBAGENT).subagent)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `CodexParser` tests FAIL with `AttributeError: module 'history' has no attribute 'parse_codex'`; earlier tests PASS.

- [ ] **Step 4: Write the implementation**

Append to `scripts/history.py`:

```python
# Codex: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
CODEX_INJECTED = ("<", "# AGENTS.md instructions")
CODEX_ANSWER = ("<send_user_message_question_reply>", "Answers to your questions:")
CODEX_SKILL = re.compile(r"^\$([a-z][\w-]*)")
SHELL_TOOLS = {"exec_command", "shell", "exec", "local_shell"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
JS_COMMAND = re.compile(r"""\bcmd["']?\s*:\s*"((?:[^"\\]|\\.)*)\"""")


def parse_codex(path):
    session = Session("codex", os.path.splitext(os.path.basename(path))[0], path)
    records = list(read_jsonl(path, session))
    # Older Codex logs a clean user_message event; newer logs only have response items.
    use_events = any(record.get("type") == "event_msg" and isinstance(record.get("payload"), dict)
                     and record["payload"].get("type") == "user_message" for record in records)
    for record in records:
        kind, payload = record.get("type"), record.get("payload")
        if kind not in ("session_meta", "event_msg", "response_item"):
            continue
        if not isinstance(payload, dict):
            session.unknown += 1
        elif kind == "session_meta":
            codex_meta(session, payload, record)
            if session.subagent:  # skipped by the loader; don't spend time parsing it
                return session
        elif kind == "event_msg":
            if payload.get("type") == "user_message" and isinstance(payload.get("message"), str):
                codex_prompt(session, payload["message"])
        else:
            codex_item(session, payload, use_events)
    tag_corrections(session)
    return session


def codex_meta(session, payload, record):
    if session.started:  # a resumed or forked thread repeats its parent's meta later in the file
        return
    git_info = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    session.id = payload.get("id") or session.id
    session.cwd = payload.get("cwd") or ""
    session.started = payload.get("timestamp") or record.get("timestamp") or ""
    session.git_url = git_info.get("repository_url") or ""
    session.branch = git_info.get("branch") or ""
    source = payload.get("source")
    session.subagent = isinstance(source, dict) and "subagent" in source


def codex_prompt(session, text):
    text = text.strip()
    skill = CODEX_SKILL.match(text)
    if skill:
        session.add("skill", "$" + skill.group(1))
    session.add("prompt", clean(text))


def codex_user_text(session, text, use_events):
    text = text.strip()
    if text.startswith(CODEX_ANSWER):
        session.add("answer", clean(text))
    elif "<turn_aborted>" in text:
        session.add("interrupt", "[turn aborted]")
    elif not use_events and not text.startswith(CODEX_INJECTED):
        codex_prompt(session, text)


def codex_item(session, payload, use_events):
    kind = payload.get("type")
    if kind == "message":
        content = payload.get("content")
        if not isinstance(content, list):
            session.unknown += 1
            return
        for part in content:
            text = part.get("text") if isinstance(part, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            if payload.get("role") == "assistant":
                session.add("reply", clean(text))
            elif payload.get("role") == "user":
                codex_user_text(session, text, use_events)
    elif kind in ("function_call", "custom_tool_call"):
        body = payload.get("arguments") if kind == "function_call" else payload.get("input")
        body = body if isinstance(body, str) else ""
        if payload.get("name") == "apply_patch":
            for path in PATCH_FILE.findall(patch_text(body)):
                session.add("edit", path.strip())
        elif payload.get("name") in SHELL_TOOLS:
            for command in codex_commands(body):
                add_command(session, command)


def patch_text(body):
    try:
        args = json.loads(body)
    except ValueError:
        return body
    return str(args.get("input") or "") if isinstance(args, dict) else body


def codex_commands(body):
    """Shell commands from JSON arguments ({"cmd": ...} or {"command": [...]}) or code-mode JS."""
    try:
        args = json.loads(body)
    except ValueError:
        args = None
    if isinstance(args, dict):
        command = args.get("cmd") or args.get("command")
        if isinstance(command, list):
            words = [str(word) for word in command]
            command = words[-1] if len(words) >= 2 and words[-2] in ("-c", "-lc") else " ".join(words)
        return [command] if isinstance(command, str) else []
    commands = []
    for raw in JS_COMMAND.findall(body):
        try:
            commands.append(json.loads(f'"{raw}"'))
        except ValueError:
            commands.append(raw)
    return commands
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 23 tests, all PASS

- [ ] **Step 6: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests
git commit -m "Parse Codex sessions into the shared session model" -m "Handles response-item and event-message prompt formats, code-mode exec commands, apply_patch edits, and flags subagent sessions." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Repo resolution

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (append after `codex_commands`)
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `normalize_remote(url) -> "host/owner/repo"`, `git(path, *args) -> str` (stdout or `""`), `git_key(path) -> str`, `load_paseo(path) -> dict[root, mainRepoRoot]`, `make_resolver(paseo_file) -> resolve(cwd, git_url="") -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


def make_repo(path, remote=None):
    os.makedirs(path)
    subprocess.run(["git", "init", "-q", path], check=True)
    if remote:
        subprocess.run(["git", "-C", path, "remote", "add", "origin", remote], check=True)
    return os.path.realpath(path)


class RepoResolution(TempDirCase):
    def resolver(self, paseo_file=None):
        return history.make_resolver(paseo_file or os.path.join(self.tmp, "missing.json"))

    def test_normalize_remote(self):
        for url in ("git@github.com:Acme/App.git", "https://github.com/acme/app", "https://github.com/acme/app/",
                    "ssh://git@github.com/acme/app.git"):
            self.assertEqual(history.normalize_remote(url), "github.com/acme/app", url)
        self.assertEqual(history.normalize_remote("ssh://git@gitlab.example.com:2222/sites/app.git"),
                         "gitlab.example.com/sites/app")

    def test_git_url_wins(self):
        self.assertEqual(self.resolver()("/nowhere", "git@github.com:Acme/App.git"), "github.com/acme/app")

    def test_live_repo_subdirectory_uses_remote(self):
        repo = make_repo(os.path.join(self.tmp, "app"), "git@github.com:acme/app.git")
        os.makedirs(os.path.join(repo, "src"))
        self.assertEqual(self.resolver()(os.path.join(repo, "src")), "github.com/acme/app")

    def test_repo_without_remote_uses_path(self):
        repo = make_repo(os.path.join(self.tmp, "local"))
        self.assertEqual(self.resolver()(repo), repo)

    def test_deleted_paseo_worktree(self):
        repo = make_repo(os.path.join(self.tmp, "main"), "https://github.com/acme/main.git")
        gone = os.path.join(self.tmp, "worktrees", "brave-fox")
        paseo = os.path.join(self.tmp, "workspaces.json")
        with open(paseo, "w") as handle:
            json.dump([{"cwd": gone, "worktreeRoot": gone, "mainRepoRoot": repo}], handle)
        self.assertEqual(self.resolver(paseo)(gone + "/pkg"), "github.com/acme/main")

    def test_deleted_conventional_worktree(self):
        repo = make_repo(os.path.join(self.tmp, "app"), "git@github.com:acme/app.git")
        self.assertEqual(self.resolver()(repo + "/.worktrees/feature-x"), "github.com/acme/app")
        self.assertEqual(self.resolver()(repo + "/.claude/worktrees/fix-y/src"), "github.com/acme/app")

    def test_unresolved_falls_back_to_path(self):
        self.assertEqual(self.resolver()("/no/such/dir"), "/no/such/dir")
        self.assertEqual(self.resolver()("/no/app/.worktrees/x/src"), "/no/app")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `RepoResolution` tests FAIL with `AttributeError: module 'history' has no attribute 'make_resolver'` (and `normalize_remote`); earlier tests PASS.

- [ ] **Step 3: Write the implementation**

Append to `scripts/history.py`:

```python
REMOTE = re.compile(r"^(?:[\w+.-]+://)?(?:[^@/]+@)?([^/:]+)[:/](?:\d+/)?(.+?)(?:\.git)?/?$")
WORKTREE_DIR = re.compile(r"^(.+?)/(?:\.worktrees|\.claude/worktrees)/[^/]+")


def normalize_remote(url):
    """git@host:o/r.git, https://host/o/r, ssh://git@host:22/o/r.git -> host/o/r."""
    match = REMOTE.match(url.strip())
    return f"{match.group(1)}/{match.group(2)}".lower() if match else url.strip()


def git(path, *args):
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def git_key(path):
    """Normalized origin URL, else the main checkout path, else '' when path is not in a repo."""
    if not path or not os.path.isdir(path):
        return ""
    remote = git(path, "remote", "get-url", "origin")
    if remote:
        return normalize_remote(remote)
    common = git(path, "rev-parse", "--git-common-dir")
    return os.path.dirname(os.path.realpath(os.path.join(path, common))) if common else ""


def load_paseo(path):
    """Paseo worktree root -> main repo root. Empty when Paseo is not installed."""
    try:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return {}
    roots = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("mainRepoRoot"):
            for key in ("worktreeRoot", "cwd"):
                if entry.get(key):
                    roots[entry[key]] = entry["mainRepoRoot"]
    return roots


def make_resolver(paseo_file):
    """Return resolve(cwd, git_url="") -> repo key, memoized per cwd."""
    paseo = load_paseo(paseo_file)
    cache = {}

    def candidates(cwd):
        paths = [cwd]
        paths += [main for root, main in paseo.items() if cwd == root or cwd.startswith(root + "/")]
        match = WORKTREE_DIR.match(cwd)
        if match:
            paths.append(match.group(1))
        return paths

    def resolve(cwd, git_url=""):
        if git_url:
            return normalize_remote(git_url)
        if cwd not in cache:
            paths = candidates(cwd)
            cache[cwd] = next((key for key in map(git_key, paths) if key), paths[-1])
        return cache[cwd]

    return resolve
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 30 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Resolve sessions to a repo key across worktrees" -m "Uses the Codex git URL, a live checkout's origin, Paseo's workspace map when present, and .worktrees conventions, falling back to the path." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Session loading, scope, `discover`, and CLI

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (append after `make_resolver`)
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `parse_claude`, `parse_codex`, `make_resolver`, `git`, `DRIFT_RATIO` (Tasks 1-4).
- Produces:
  - `default_dir(env, fallback) -> str`, `history_stores(args) -> list[(agent, home, glob_pattern, parse)]`.
  - `project_root(path) -> str` (git toplevel or the path, realpath'd), `scope_label(args) -> "global" | "project <root>"`, `in_project(session, key, root) -> bool`.
  - `load_sessions(args) -> (sessions: list[Session] sorted by started, notes: list[str])`; sets `session.repo`.
  - `cmd_discover(args) -> str`.
  - `main(argv=None) -> int`: subcommands registered with `commands.add_parser(name, parents=[common], help=...).set_defaults(handler=cmd_x)`; `args` fields: `project` (path or `None`), `all` (bool), `since`, `limit`, `claude_dir`, `codex_dir`, `paseo_file`. Neither scope flag → `args.project = "."`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
MISSING = os.path.join(FIXTURES, "missing")
FIXTURE_ARGS = ["--claude-dir", CLAUDE_DIR, "--codex-dir", CODEX_DIR, "--paseo-file", MISSING]


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        history.main(list(argv))
    return out.getvalue()


def write_session(claude_dir, name, records):
    folder = os.path.join(claude_dir, "projects", "-tmp")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".jsonl"), "wb") as handle:
        for record in records:
            handle.write(record if isinstance(record, bytes) else json.dumps(record).encode() + b"\n")


def human(cwd, text):
    return {"type": "user", "cwd": cwd, "timestamp": "2026-09-01T00:00:00Z", "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


class Discover(TempDirCase):
    def rows(self, output):
        return [line for line in output.splitlines()[1:] if line and not line.startswith("#")]

    def test_global_lists_sessions_and_skips_subagents(self):
        out = run("discover", "--global", *FIXTURE_ARGS)
        self.assertEqual(len(self.rows(out)), 4)
        self.assertIn("# skipped 1 subagent sessions", out)
        self.assertIn("github.com/acme/app", out)

    def test_project_groups_worktree_with_checkout(self):
        out = run("discover", "--project", "/fixture/app", *FIXTURE_ARGS)
        ids = sorted(line.split("\t")[1][:13] for line in self.rows(out))
        self.assertEqual(ids, ["0188bbbb-0002", "c1a0de00-0001", "c1a0de00-0002"])

    def test_since_filters_by_start_date(self):
        self.assertEqual(len(self.rows(run("discover", "--global", "--since", "2026-09-01", *FIXTURE_ARGS))), 3)

    def test_missing_store_is_noted(self):
        out = run("discover", "--global", "--claude-dir", CLAUDE_DIR, "--codex-dir", MISSING, "--paseo-file", MISSING)
        self.assertIn(f"# codex: no history at {MISSING}", out)
        self.assertEqual(len(self.rows(out)), 2)

    def test_empty_scope_lists_nearest_cwds(self):
        out = run("discover", "--project", "/elsewhere/none", *FIXTURE_ARGS)
        self.assertEqual(self.rows(out), [])
        self.assertIn("no sessions in scope", out)
        self.assertIn("/fixture/app", out)

    def test_drift_warning_without_crash(self):
        claude = os.path.join(self.tmp, "claude")
        write_session(claude, "s1", [human("/w", "hello there agent friend"), b"not json\n", b"{broken\n", b"\xff\xfe\n"])
        out = run("discover", "--global", "--claude-dir", claude, "--codex-dir", MISSING, "--paseo-file", MISSING)
        self.assertIn("log format may have changed", out)
        self.assertEqual(len(self.rows(out)), 1)

    def test_continued_session_not_double_counted(self):
        claude = os.path.join(self.tmp, "claude")
        first = dict(human("/w", "deploy the app to staging now"), uuid="u1")
        write_session(claude, "a-original", [first])
        write_session(claude, "b-continued", [first, dict(human("/w", "and tag the release"), uuid="u2")])
        out = run("discover", "--global", "--claude-dir", claude, "--codex-dir", MISSING, "--paseo-file", MISSING)
        self.assertEqual(sorted(int(line.split("\t")[7]) for line in self.rows(out)), [1, 1])

    def test_project_subdirectory_resolves_to_repo(self):
        repo = make_repo(os.path.join(self.tmp, "app"), "git@github.com:acme/sub.git")
        os.makedirs(os.path.join(repo, "src"))
        claude = os.path.join(self.tmp, "claude")
        write_session(claude, "s1", [human(repo, "run the tests")])
        out = run("discover", "--project", os.path.join(repo, "src") + "/",
                  "--claude-dir", claude, "--codex-dir", MISSING, "--paseo-file", MISSING)
        self.assertEqual(len(self.rows(out)), 1)
        self.assertIn("github.com/acme/sub", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Discover` tests FAIL with `AttributeError: module 'history' has no attribute 'main'`; earlier tests PASS.

- [ ] **Step 3: Write the implementation**

Append to `scripts/history.py`:

```python
def default_dir(env, fallback):
    return os.environ.get(env) or os.path.expanduser(fallback)


def history_stores(args):
    seen = set()  # Claude message uuids shared across files, so continued sessions are not double-counted
    return [
        ("claude", args.claude_dir, os.path.join(args.claude_dir, "projects", "*", "*.jsonl"),
         lambda path: parse_claude(path, seen)),
        ("codex", args.codex_dir, os.path.join(args.codex_dir, "sessions", "**", "*.jsonl"), parse_codex),
    ]


def project_root(path):
    return os.path.realpath(git(path, "rev-parse", "--show-toplevel") or path)


def scope_label(args):
    return "global" if args.all else f"project {project_root(args.project)}"


def in_project(session, key, root):
    cwd = os.path.realpath(session.cwd) if session.cwd else ""
    return session.repo == key or cwd == root or cwd.startswith(root + "/")


def load_sessions(args):
    """Parse every session in scope; return (sessions sorted by start, notes for the reader)."""
    resolve = make_resolver(args.paseo_file)
    root = None if args.all else project_root(args.project)
    key = resolve(root) if root else None
    sessions, notes, outside, drifted, skipped = [], [], set(), [], 0
    for agent, home, pattern, parse in history_stores(args):
        if not os.path.isdir(home):
            notes.append(f"{agent}: no history at {home}")
            continue
        parsed = [parse(path) for path in sorted(glob.glob(pattern, recursive=True))]
        if parsed and not any(session.prompts() for session in parsed):
            notes.append(f"warning: no prompts recognized in {len(parsed)} {agent} sessions"
                         " - log format may have changed")
        for session in parsed:
            if session.subagent:
                skipped += 1
                continue
            if session.total and session.unknown / session.total > DRIFT_RATIO:
                drifted.append(session.short)
            if args.since and session.started[:10] < args.since:
                continue
            session.repo = resolve(session.cwd, session.git_url)
            if root is None or in_project(session, key, root):
                sessions.append(session)
            elif session.cwd:
                outside.add(session.cwd)
    if skipped:
        notes.append(f"skipped {skipped} subagent sessions")
    if drifted:
        notes.append(f"warning: {len(drifted)} sessions have over {DRIFT_RATIO:.0%} unrecognized records"
                     f" (e.g. {', '.join(drifted[:3])}) - log format may have changed")
    if root and not sessions:
        nearest = sorted(outside, key=lambda cwd: (-len(os.path.commonprefix([cwd, root])), cwd))[:5]
        notes.append(f"no sessions in scope for {root}; nearest session cwds: {', '.join(nearest) or 'none'}")
    sessions.sort(key=lambda session: session.started)
    return sessions, notes


def cmd_discover(args):
    sessions, notes = load_sessions(args)
    lines = ["agent\tsession\tpath\trepo\tcwd\tbranch\tstarted\tprompts"]
    for s in sessions:
        lines.append("\t".join([s.agent, s.id, s.path, s.repo, s.cwd, s.branch, s.started, str(len(s.prompts()))]))
    lines += [f"# {note}" for note in notes]
    path_keyed = sorted({s.repo for s in sessions if os.path.isabs(s.repo)})
    if path_keyed:
        lines.append(f"# repos keyed by path, no git remote found ({len(path_keyed)}): {', '.join(path_keyed[:10])}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="history.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    scope = common.add_mutually_exclusive_group()
    scope.add_argument("--project", nargs="?", const=".", metavar="PATH",
                       help="one repo or directory, worktrees included (default: current directory)")
    scope.add_argument("--global", dest="all", action="store_true", help="every repo")
    common.add_argument("--since", metavar="YYYY-MM-DD", help="only sessions started on or after this date")
    common.add_argument("--limit", type=int, default=40, help="max rows per table (default: 40)")
    common.add_argument("--claude-dir", default=default_dir("CLAUDE_CONFIG_DIR", "~/.claude"),
                        help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    common.add_argument("--codex-dir", default=default_dir("CODEX_HOME", "~/.codex"),
                        help="Codex home (default: $CODEX_HOME or ~/.codex)")
    common.add_argument("--paseo-file", default=os.path.expanduser("~/.paseo/projects/workspaces.json"),
                        help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover", parents=[common],
                        help="list sessions in scope with resolved repos").set_defaults(handler=cmd_discover)
    args = parser.parse_args(argv)
    if not args.all and args.project is None:
        args.project = "."
    print(args.handler(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 38 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Add session loading, scope filtering, and the discover command" -m "Skips subagent sessions, warns on log-format drift, and lists the nearest cwds when a project has no sessions." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `stats`

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (insert above `def main(`; register the subcommand in `main`)
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `load_sessions`, `scope_label`, `cell`, `command_prefixes`, `SIMILARITY`, `MIN_SHINGLES`, `CITES_PER_ROW` (Tasks 1-5).
- Produces:
  - `shingles(text) -> set[str]`, `jaccard(a, b) -> float`, `group_similar(entries) -> list[list[(Session, Turn)]]`.
  - `cite(entries) -> str` (one `"agent:id13 tN"` per session, max 6, then `+N`).
  - `group_rows(groups, min_sessions, limit) -> list[(sessions, repos, example, cites)]`.
  - `session_counts(sessions, kind, values) -> dict[value, set[(session_id, repo)]]`, `count_rows(counts, limit, min_sessions=2) -> list[(sessions, repos, value)]`.
  - `relative_edit(session, turn) -> list[str]`.
  - `table(title, header, rows) -> str` (Markdown section; `(none)` when empty). Used again by Task 8.
  - `cmd_stats(args) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
class Stats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run("stats", "--global", *FIXTURE_ARGS)

    def section(self, title, out=None):
        return (out or self.out).split(f"## {title}")[1].split("\n## ")[0]

    def test_header(self):
        self.assertIn("# Stats - global", self.out)
        self.assertIn("4 sessions, 7 prompts, 2 repos, 3 corrections", self.out)

    def test_repeated_prompts_grouped_across_agents(self):
        section = self.section("Repeated prompts")
        self.assertIn("| 3 | 2 | Merge the feature branch into dev and push dev to origin |", section)
        self.assertNotIn("orders table", section)

    def test_corrections_listed(self):
        section = self.section("Corrections")
        self.assertIn("No, don't touch the vendor directory again", section)
        self.assertIn("use the release branch instead", section)

    def test_counts_by_distinct_sessions(self):
        self.assertIn("| 2 | 2 | git push |", self.section("Commands"))
        self.assertIn("| 2 | 2 | working-with-git |", self.section("Skills"))
        self.assertIn("| 2 | 2 | src/app.py |", self.section("Edited files"))

    def test_limit_caps_rows(self):
        out = run("stats", "--global", "--limit", "1", *FIXTURE_ARGS)
        rows = [line for line in self.section("Corrections", out).splitlines() if line.startswith("| ")]
        self.assertEqual(len(rows), 2)  # header + one row

    def test_group_similar_uses_trigram_overlap(self):
        a, b = history.Session("claude", "a", "p"), history.Session("codex", "b", "p")
        entries = [(a, history.Turn(0, "prompt", "deploy the app to staging now")),
                   (b, history.Turn(0, "prompt", "please deploy the app to staging now")),
                   (b, history.Turn(1, "prompt", "write a migration for orders"))]
        self.assertEqual([len(group) for group in history.group_similar(entries)], [2, 1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Stats` tests ERROR — argparse exits with `invalid choice: 'stats'` (SystemExit 2) and `group_similar` is missing; earlier tests PASS.

- [ ] **Step 3: Write the implementation**

Insert above `def main(` in `scripts/history.py`:

```python
WORD = re.compile(r"[a-z][a-z'’-]+")
PATH_OR_NUMBER = re.compile(r"\S*[/\\]\S*|\d+")


def shingles(text):
    """Word trigrams with paths, numbers, and one-letter words removed."""
    words = WORD.findall(PATH_OR_NUMBER.sub(" ", text.lower()))
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def group_similar(entries):
    """Greedy grouping of (session, turn) entries; a group's first entry is its representative."""
    groups, index = [], collections.defaultdict(set)
    for entry in entries:
        marks = shingles(entry[1].text)
        candidates = sorted(set().union(*(index[mark] for mark in marks)))
        home = next((g for g in candidates if jaccard(marks, groups[g][0]) >= SIMILARITY), None)
        if home is None:
            home = len(groups)
            groups.append((marks, []))
            for mark in marks:
                index[mark].add(home)
        groups[home][1].append(entry)
    return [members for _, members in groups]


def cite(entries):
    seen, cites = set(), []
    for session, turn in entries:
        if session.id not in seen:
            seen.add(session.id)
            cites.append(f"{session.short} t{turn.index}")
    extra = len(cites) - CITES_PER_ROW
    return ", ".join(cites[:CITES_PER_ROW]) + (f" +{extra}" if extra > 0 else "")


def group_rows(groups, min_sessions, limit):
    rows = []
    for members in groups:
        sessions = {session.id for session, _ in members}
        if len(sessions) >= min_sessions:
            repos = {session.repo for session, _ in members}
            rows.append((len(sessions), len(repos), cell(members[0][1].text), cite(members)))
    rows.sort(key=lambda row: (-row[0], -row[1]))
    return rows[:limit]


def session_counts(sessions, kind, values):
    counts = collections.defaultdict(set)
    for session in sessions:
        for turn in session.turns:
            if turn.kind == kind:
                for value in values(session, turn):
                    counts[value].add((session.id, session.repo))
    return counts


def count_rows(counts, limit, min_sessions=2):
    rows = []
    for value, hits in counts.items():
        sessions = {session_id for session_id, _ in hits}
        if len(sessions) >= min_sessions:
            rows.append((len(sessions), len({repo for _, repo in hits}), cell(value)))
    rows.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return rows[:limit]


def relative_edit(session, turn):
    base = session.cwd.rstrip("/") + "/"
    return [turn.text[len(base):] if session.cwd and turn.text.startswith(base) else turn.text]


def table(title, header, rows):
    lines = [f"## {title}", ""]
    if not rows:
        return "\n".join(lines + ["(none)", ""])
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    return "\n".join(lines + [""])


def cmd_stats(args):
    sessions, notes = load_sessions(args)
    prompts = [(session, turn) for session in sessions for turn in session.prompts()]
    corrections = [(session, turn) for session, turn in prompts if turn.correction]
    groupable = [(session, turn) for session, turn in prompts if len(shingles(turn.text)) >= MIN_SHINGLES]
    grouped = ["sessions", "repos", "example", "cites"]
    return "\n".join([
        f"# Stats - {scope_label(args)}",
        "",
        f"{len(sessions)} sessions, {len(prompts)} prompts, {len({s.repo for s in sessions})} repos,"
        f" {len(corrections)} corrections",
        *[f"- {note}" for note in notes],
        "",
        table("Repeated prompts (>=2 sessions)", grouped, group_rows(group_similar(groupable), 2, args.limit)),
        table("Corrections", grouped, group_rows(group_similar(corrections), 1, args.limit)),
        table("Commands", ["sessions", "repos", "prefix"],
              count_rows(session_counts(sessions, "command", lambda s, t: set(command_prefixes(t.text))), args.limit)),
        table("Skills and slash commands", ["sessions", "repos", "skill"],
              count_rows(session_counts(sessions, "skill", lambda s, t: [t.text]), args.limit)),
        table("Edited files", ["sessions", "repos", "file"],
              count_rows(session_counts(sessions, "edit", relative_edit), args.limit)),
    ])
```

In `main()`, directly after the `discover` registration, add:

```python
    commands.add_parser("stats", parents=[common],
                        help="repeated prompts, corrections, commands, skills, edited files").set_defaults(handler=cmd_stats)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 44 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Add the stats command" -m "Groups near-duplicate prompts and corrections by word-trigram overlap and counts commands, skills, and edited files by distinct session, each row citing sessions." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `digest`

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (insert above `def main(`; register the subcommand in `main`)
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `load_sessions`, `command_prefixes`, `clip`, `REPLY_CONTEXT`, `Session.short` (Tasks 1-5).
- Produces: `render_digest(session) -> str`, `cmd_digest(args) -> str`; `args.ids: list[str]`, each `agent:prefix` or `prefix`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
class Digest(unittest.TestCase):
    def test_renders_session_with_correction_context(self):
        out = run("digest", "claude:c1a0de00-0001", "--global", *FIXTURE_ARGS)
        self.assertIn("## claude:c1a0de00-0001 - 2026-09-01 - /fixture/app - main", out)
        self.assertIn("t0 prompt: Merge the feature branch into dev and push dev to origin", out)
        self.assertIn("prompt [correction]: No, don't touch the vendor directory again", out)
        self.assertIn("(reply before) I will run the deploy script now.", out)
        self.assertIn("command: git push", out)
        self.assertNotIn("SIDECHAIN", out)
        self.assertNotIn("sk-abc", out)

    def test_prefix_without_agent(self):
        self.assertIn("## codex:0199aaaa-0001", run("digest", "0199aaaa", "--global", *FIXTURE_ARGS))

    def test_unknown_id(self):
        self.assertIn("(no session in scope matches)", run("digest", "nope", "--global", *FIXTURE_ARGS))

    def test_collapses_repeated_commands(self):
        session = history.Session("claude", "x", "p")
        for _ in range(3):
            session.add("command", "git status")
        self.assertIn("t0 command: git status (x3)", history.render_digest(session))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Digest` tests ERROR — `invalid choice: 'digest'` (SystemExit 2) and `render_digest` missing; earlier tests PASS.

- [ ] **Step 3: Write the implementation**

Insert above `def main(` in `scripts/history.py`:

```python
def render_digest(session):
    lines = [f"## {session.short} - {session.started[:10]} - {session.repo or '?'} - {session.branch or '?'}", ""]
    last_reply, last_entry, repeats, base_line = "", None, 1, ""
    for turn in session.turns:
        if turn.kind == "reply":
            last_reply = turn.text
            continue
        text = ", ".join(command_prefixes(turn.text)) if turn.kind == "command" else turn.text
        if (turn.kind, text) == last_entry and turn.kind != "prompt":
            repeats += 1
            lines[-1] = f"{base_line} (x{repeats})"
            continue
        last_entry, repeats = (turn.kind, text), 1
        if turn.correction and last_reply:
            lines.append(f"    (reply before) {' '.join(clip(last_reply, REPLY_CONTEXT).split())}")
        tag = " [correction]" if turn.correction else ""
        base_line = f"t{turn.index} {turn.kind}{tag}: {text}".replace("\n", "\n    ")
        lines.append(base_line)
    return "\n".join(lines) + "\n"


def cmd_digest(args):
    sessions, notes = load_sessions(args)
    blocks = [f"- {note}" for note in notes]
    for wanted in args.ids:
        agent, _, prefix = wanted.rpartition(":")
        matches = [s for s in sessions if s.id.startswith(prefix) and agent in ("", s.agent)]
        blocks += [render_digest(s) for s in matches] or [f"## {wanted}\n\n(no session in scope matches)\n"]
    return "\n".join(blocks)
```

In `main()`, directly after the `stats` registration, add:

```python
    digest = commands.add_parser("digest", parents=[common], help="compact transcript of chosen sessions")
    digest.add_argument("ids", nargs="+", metavar="SESSION", help="session id or prefix, optionally agent:prefix")
    digest.set_defaults(handler=cmd_digest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 48 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Add the digest command" -m "Renders chosen sessions as numbered turns with correction context and collapsed repeated commands." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `inventory`

**Files:**
- Modify: `mine/mining-agent-history/scripts/history.py` (insert above `def main(`; register the subcommand in `main`)
- Modify: `mine/mining-agent-history/tests/test_history.py`

**Interfaces:**
- Consumes: `project_root`, `scope_label`, `table`, `cell`, `redact` (Tasks 1, 5, 6).
- Produces: `frontmatter(path) -> dict`, `skill_roots(args, root) -> list[str]`, `instruction_files(args, root) -> list[str]`, `cmd_inventory(args) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` above the `if __name__` block:

```python
def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


class Inventory(TempDirCase):
    def setUp(self):
        super().setUp()
        self.claude = os.path.join(self.tmp, "claude")
        self.codex = os.path.join(self.tmp, "codex")
        self.project = os.path.join(self.tmp, "project")
        write_file(os.path.join(self.claude, "skills", "alpha", "SKILL.md"),
                   "---\nname: alpha\ndescription: >\n  Line one\n  line two\n---\n# Alpha\n")
        write_file(os.path.join(self.claude, "plugins", "cache", "mkt", "plug", "1.0", "skills", "beta", "SKILL.md"),
                   "---\nname: beta\ndescription: Beta skill | piped\n---\n")
        write_file(os.path.join(self.codex, "skills", "alpha", "SKILL.md"), "---\nname: alpha\ndescription: duplicate\n---\n")
        write_file(os.path.join(self.claude, "CLAUDE.md"), "Global rules.\n@RTK.md\n")
        write_file(os.path.join(self.codex, "AGENTS.md"), "Codex global rules.\n")
        write_file(os.path.join(self.project, "AGENTS.md"), "Project rules.\n")
        os.symlink("AGENTS.md", os.path.join(self.project, "CLAUDE.md"))
        write_file(os.path.join(self.project, ".claude", "skills", "gamma", "SKILL.md"),
                   "---\nname: gamma\ndescription: Gamma\n---\n")
        patcher = mock.patch.dict(os.environ, {"HOME": os.path.join(self.tmp, "home")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def inventory(self, *scope):
        return run("inventory", *scope, "--claude-dir", self.claude, "--codex-dir", self.codex, "--paseo-file", MISSING)

    def test_global(self):
        out = self.inventory("--global")
        self.assertIn("| alpha | Line one line two |", out)
        self.assertIn("| beta | Beta skill \\| piped |", out)
        self.assertNotIn("duplicate", out)
        self.assertNotIn("gamma", out)
        self.assertIn("Imports (not inlined): RTK.md", out)
        self.assertIn("Codex global rules.", out)
        self.assertNotIn("Project rules.", out)

    def test_project_adds_project_skills_and_files_once(self):
        out = self.inventory("--project", self.project)
        self.assertIn("| gamma | Gamma |", out)
        self.assertIn("| alpha |", out)
        self.assertEqual(out.count("Project rules."), 1)
        self.assertIn("Global rules.", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Inventory` tests ERROR — `invalid choice: 'inventory'` (SystemExit 2); earlier tests PASS.

- [ ] **Step 3: Write the implementation**

Insert above `def main(` in `scripts/history.py`:

```python
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.S)
IMPORT = re.compile(r"(?:^|\s)@([~\w./-]+\.md)\b", re.M)


def frontmatter(path):
    """name/description from SKILL.md frontmatter, including folded multi-line values."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            match = FRONTMATTER.match(handle.read(16384))
    except OSError:
        return {}
    fields, key = {}, None
    for line in match.group(1).splitlines() if match else []:
        pair = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if pair:
            key, value = pair.group(1), pair.group(2).strip()
            fields[key] = "" if re.fullmatch(r"[>|][-+]?", value) else value.strip("'\"")
        elif key and line[:1].isspace():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return fields


def skill_roots(args, root):
    roots = [os.path.join(args.claude_dir, "skills"), os.path.join(args.codex_dir, "skills"),
             os.path.expanduser("~/.agents/skills")]
    roots += sorted(glob.glob(os.path.join(args.claude_dir, "plugins", "cache", "*", "*", "*", "skills")))
    if root:
        roots += [os.path.join(root, ".claude", "skills"), os.path.join(root, ".agents", "skills")]
    return roots


def instruction_files(args, root):
    files = [os.path.join(args.claude_dir, "CLAUDE.md"), os.path.join(args.codex_dir, "AGENTS.md")]
    if root:
        files += [os.path.join(root, "AGENTS.md"), os.path.join(root, "CLAUDE.md")]
    return files


def cmd_inventory(args):
    root = None if args.all else project_root(args.project)
    rows, names = [], set()
    for skills_dir in skill_roots(args, root):
        for skill_file in sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md"))):
            meta = frontmatter(skill_file)
            name = meta.get("name") or os.path.basename(os.path.dirname(skill_file))
            if name not in names:
                names.add(name)
                rows.append((cell(name), cell(meta.get("description", "")), skills_dir))
    out = [f"# Inventory - {scope_label(args)}", "", table("Skills", ["name", "description", "source"], rows)]
    shown = set()
    for path in instruction_files(args, root):
        real = os.path.realpath(path)
        if real in shown or not os.path.isfile(path):
            continue
        shown.add(real)
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        out.append(f"## {path}\n")
        imports = IMPORT.findall(text)
        if imports:
            out.append(f"Imports (not inlined): {', '.join(imports)}\n")
        out.append(f"````markdown\n{redact(text).rstrip()}\n````\n")
    if not shown:
        out.append("## Instruction files\n\n(none found)\n")
    return "\n".join(out)
```

In `main()`, directly after the `digest` registration, add:

```python
    commands.add_parser("inventory", parents=[common],
                        help="existing skills and instruction files").set_defaults(handler=cmd_inventory)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: 50 tests, all PASS

- [ ] **Step 5: Commit**

```bash
git add mine/mining-agent-history/scripts/history.py mine/mining-agent-history/tests/test_history.py
git commit -m "Add the inventory command" -m "Lists existing skills from standard Claude, Codex, and shared skill directories plus instruction files, so candidates can be checked for overlap." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: SKILL.md, writing guide, and real-history verification

**Files:**
- Create: `mine/mining-agent-history/SKILL.md`
- Create: `mine/mining-agent-history/references/writing-guide.md`

**Interfaces:**
- Consumes: the CLI from Tasks 5-8 (`discover`, `stats`, `digest`, `inventory`; flags `--project [PATH]`, `--global`, `--since`, `--limit`, `--claude-dir`, `--codex-dir`; cite format `agent:id13 tN`).
- Produces: the installable skill.

- [ ] **Step 1: Write `SKILL.md`**

~~~~markdown
---
name: mining-agent-history
description: Mine Claude Code and Codex chat history for repeated tasks and repeated asks, then propose new skills and AGENTS.md or CLAUDE.md rules backed by evidence. Use when asked to review past agent sessions, find skill candidates, refine or prune AGENTS.md or CLAUDE.md from history, or find what the agent keeps being told.
---

# Mining agent history

Find what keeps coming up in past Claude Code and Codex sessions, and propose skills and instruction rules for it. This workflow only proposes: never create skills or edit instruction files here.

Scripts do the reading. Never open session JSONL directly; it is mostly injected noise. Run the script from this skill's directory (Python 3.9+, standard library only):

```
python3 scripts/history.py <subcommand> [--project [PATH] | --global] [--since YYYY-MM-DD] [--limit N]
```

| Subcommand | Output |
|---|---|
| `inventory` | existing skills and instruction files in scope |
| `stats` | repeated prompts, corrections, commands, skills, edited files; every row cites sessions as `agent:id tN` |
| `digest ID...` | compact transcript of chosen sessions; pass the `agent:id` part of a cite |
| `discover` | every session in scope with its resolved repo; use to debug scope |

History is read from `$CLAUDE_CONFIG_DIR` (default `~/.claude`) and `$CODEX_HOME` (default `~/.codex`). Override with `--claude-dir` and `--codex-dir`.

Use the strongest model available. The input is small; the work is judgment.

## Workflow

1. **Scope.** Default is project mode: the current repo, including its worktrees. Use `--global` when the user asks about all projects or general habits.
2. **Read** `inventory`, then `stats`. Read every `warning:` line first. A warning means the log format may have changed and results may be incomplete; tell the user.
3. **Form hypotheses** from `stats`:
   - Repeated-prompt group: skill candidate.
   - Correction group: rule candidate.
   - Same command prefixes across sessions: skill candidate, possibly with a script.
4. **Verify** each hypothesis: run `digest` on at most 5 of its cited sessions. Drop it when it is one session's back-and-forth rather than a repeat across sessions.
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
   - Global mode: skip project-specific candidates.
7. **Rank** by sessions affected, then correction count, then turns spent per occurrence.
8. **Report** in chat using the format below, then stop. Write the report to a file only when the user asks: quotes come from private transcripts.

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
~~~~

- [ ] **Step 2: Write `references/writing-guide.md`**

~~~~markdown
# Writing guide

Minimal rules for turning a report candidate into a skill or an instruction rule.

## Skill

- One kebab-case directory per skill, containing `SKILL.md`.
- Frontmatter has two fields:

  ```yaml
  ---
  name: deploying-to-staging
  description: Deploy the current branch to staging. Use when asked to deploy, ship to staging, or push a build for QA.
  ---
  ```

- `description` states what the skill does and when to use it, in the words a user would type. Agents choose skills from this line alone.
- Body: imperative steps in order, with exact commands, paths, and checks. Leave out background the agent already knows.
- Put deterministic or repeated mechanics in `scripts/` and call them from the steps.
- Keep `SKILL.md` short. Move long reference material into `references/` and link it.
- Location: project skills in `<repo>/.claude/skills/` or `<repo>/.agents/skills/`; personal skills in `~/.claude/skills/`, `~/.codex/skills/`, or `~/.agents/skills/`.

## Instruction rule (AGENTS.md or CLAUDE.md)

- One rule per bullet, imperative: "Run `make test` before committing."
- Add the reason when it is not obvious: "Never edit `vendor/`: deploys regenerate it."
- Place the rule under an existing heading when one fits. Add a heading only for a new topic.
- To strengthen an ignored rule, make it specific and checkable and move it near the top. Emphasis alone does not help.
- To prune, remove rules that no longer apply. A short file gets read in full.
- In projects, prefer `AGENTS.md`. Codex reads it; Claude Code reads `CLAUDE.md`, which can import it with a line `@AGENTS.md` or be a symlink to it.
~~~~

- [ ] **Step 3: Link the skill and run the full suite**

Run from `~/.agents`: `bin/link-mine`
Expected: `linked N skill(s) from mine/` with N one higher than before; `skills/mining-agent-history` is a symlink to `../mine/mining-agent-history`.

Run from the skill directory: `python3 -m unittest discover -s tests -v`
Expected: 50 tests, all PASS

- [ ] **Step 4: Verify on real history**

Run from the skill directory:

```bash
python3 -I scripts/history.py discover --project ~/.agents | tail -5
```

If the notes include `claude: no history at`, `CLAUDE_CONFIG_DIR` is unset in this shell. Add `--claude-dir ~/.config/claude` to every command below.

```bash
time python3 -I scripts/history.py stats --project ~/.agents > /tmp/mah-project.md
time python3 -I scripts/history.py stats --global > /tmp/mah-global.md
grep -cE '<system-reminder>|Base directory for this skill|# AGENTS.md instructions|<environment_context>' /tmp/mah-project.md /tmp/mah-global.md
grep -cE 'sk-[A-Za-z0-9_-]{16,}|gh[opusr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' /tmp/mah-project.md /tmp/mah-global.md
python3 -I scripts/history.py inventory --global | grep -c 'mining-agent-history'
```

Expected:
- Each `stats` run finishes in under 10 s (`real` time).
- Both `grep -c` noise and secret counts are `0` for both files.
- `inventory` count is `1`.
- `/tmp/mah-global.md` has a non-empty `Repeated prompts` table. If it shows `(none)`, report that to the user with the prompt and correction counts from the header; do not change `SIMILARITY` without approval.

Read both stats files, then delete them: `rm /tmp/mah-project.md /tmp/mah-global.md`.

- [ ] **Step 5: Run the skill end to end**

Follow `SKILL.md` in project mode on `~/.agents`. Confirm every evidence cite in the report matches a session that `digest` can render. Show the report to the user.

- [ ] **Step 6: Commit**

```bash
git add mine/mining-agent-history/SKILL.md mine/mining-agent-history/references/writing-guide.md
git commit -m "Add mining-agent-history skill instructions and writing guide" -m "The workflow reads inventory and stats, verifies candidates with digests, routes them to skills or instruction rules, and reports without writing anything." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
