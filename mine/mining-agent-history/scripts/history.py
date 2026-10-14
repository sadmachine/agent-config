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
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse

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
MULTILINE_STRING = re.compile(r"""(["'])(?:(?!\1)[^\\\n]|\\.)*\n(?:(?!\1)[^\\]|\\.)*\1""")
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
    """Program plus subcommand of each simple command; skips heredoc bodies, multi-line strings, shell syntax, and wrappers."""
    prefixes = []
    for segment in COMMAND_SPLIT.split(MULTILINE_STRING.sub('""', HEREDOC.sub("", command))):
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


# Newer Codex keeps thread history in SQLite and deletes old rollout files:
# $CODEX_HOME/state_N.sqlite (threads) and $CODEX_HOME/thread_history_N.sqlite (thread_items, thread_turns).
THREAD_ITEMS = """
    select i.turn_id, i.item_type, i.item_json, t.status from thread_items i
    left join thread_turns t on t.thread_id = i.thread_id and t.turn_id = i.turn_id
    where i.thread_id = ? order by i.rollout_ordinal"""


def latest_db(codex_dir, prefix):
    """Highest-versioned database: Codex bumps the numeric suffix on schema changes."""
    found = glob.glob(os.path.join(codex_dir, f"{prefix}_*.sqlite"))
    versions = [(int(match.group(1)), path) for path in found
                for match in [re.search(r"_(\d+)\.sqlite$", path)] if match]
    return max(versions)[1] if versions else ""


def open_readonly(path):
    uri = "file:" + urllib.parse.quote(path)
    try:
        db = sqlite3.connect(uri + "?mode=ro", uri=True)
        db.execute("select 1 from sqlite_master").fetchall()
        return db
    except sqlite3.Error:  # no write access to the WAL index: read the file as-is, skipping uncommitted writes
        return sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)


def parse_codex_db(codex_dir, known_ids):
    """Rebuild threads whose rollout file is gone and that no parsed rollout covers. Returns (sessions, notes)."""
    state_path, history_path = latest_db(codex_dir, "state"), latest_db(codex_dir, "thread_history")
    if not (state_path and history_path):
        return [], []
    sessions = []
    try:
        threads = open_readonly(state_path).execute(
            "select id, rollout_path, cwd, git_origin_url, git_branch, created_at, source from threads").fetchall()
        history_db = open_readonly(history_path)
        for thread_id, rollout, cwd, git_url, branch, created, source in threads:
            if thread_id in known_ids or (rollout and os.path.exists(rollout)):
                continue
            session = Session("codex", thread_id, history_path, cwd=cwd or "", branch=branch or "",
                              git_url=git_url or "", subagent=codex_db_subagent(source),
                              started=datetime.datetime.fromtimestamp(created or 0, datetime.timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"))
            if not session.subagent:
                codex_db_turns(session, history_db.execute(THREAD_ITEMS, (thread_id,)))
                tag_corrections(session)
            sessions.append(session)
    except sqlite3.Error as error:
        return [], [f"warning: could not read Codex history database ({error}) - log format may have changed"]
    return sessions, []


def codex_db_subagent(source):
    try:
        parsed = json.loads(source or "")
    except ValueError:
        return False
    return isinstance(parsed, dict) and "subagent" in parsed


def codex_db_turns(session, rows):
    previous_turn, previous_status = None, None
    for turn_id, item_type, item_json, status in rows:
        if turn_id != previous_turn and previous_status == "interrupted":
            session.add("interrupt", "[turn interrupted]")
        previous_turn, previous_status = turn_id, status
        session.total += 1
        try:
            item = json.loads(item_json)
        except (TypeError, ValueError):
            item = None
        if not isinstance(item, dict):
            session.unknown += 1
        elif item_type == "userMessage":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "skill":
                    session.add("skill", "$" + str(part.get("name") or ""))
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    codex_user_text(session, part["text"], use_events=False)
        elif item_type == "agentMessage" and isinstance(item.get("text"), str):
            session.add("reply", clean(item["text"]))
        elif item_type == "commandExecution" and isinstance(item.get("command"), str):
            add_command(session, item["command"])
        elif item_type == "fileChange":
            for change in item.get("changes") or []:
                if isinstance(change, dict):
                    session.add("edit", str(change.get("path") or ""))
    if previous_status == "interrupted":
        session.add("interrupt", "[turn interrupted]")


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


def default_dir(env, fallback):
    return os.environ.get(env) or os.path.expanduser(fallback)


def history_stores(args):
    """(agent, home, load); load() returns (sessions, notes)."""
    return [("claude", args.claude_dir, lambda: (load_claude(args.claude_dir), [])),
            ("codex", args.codex_dir, lambda: load_codex(args.codex_dir))]


def load_claude(claude_dir):
    seen = set()  # message uuids shared across files, so continued sessions are not double-counted
    return [parse_claude(path, seen) for path in sorted(glob.glob(os.path.join(claude_dir, "projects", "*", "*.jsonl")))]


def load_codex(codex_dir):
    """Rollout JSONL (live and archived), plus threads whose rollout Codex has since deleted."""
    pattern = os.path.join(codex_dir, "*sessions", "**", "*.jsonl")  # sessions/ and archived_sessions/
    sessions = [parse_codex(path) for path in sorted(glob.glob(pattern, recursive=True))]
    rebuilt, notes = parse_codex_db(codex_dir, {session.id for session in sessions})
    return sessions + rebuilt, notes


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
    for agent, home, load in history_stores(args):
        if not os.path.isdir(home):
            notes.append(f"{agent}: no history at {home}")
            continue
        parsed, store_notes = load()
        notes += store_notes
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
    """Project roots first: a project skill shadows a global skill of the same name."""
    roots = [os.path.join(root, ".claude", "skills"), os.path.join(root, ".agents", "skills")] if root else []
    roots += [os.path.join(args.claude_dir, "skills"), os.path.join(args.codex_dir, "skills"),
              os.path.expanduser("~/.agents/skills")]
    return roots + sorted(glob.glob(os.path.join(args.claude_dir, "plugins", "cache", "*", "*", "*", "skills")))


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
    commands.add_parser("stats", parents=[common],
                        help="repeated prompts, corrections, commands, skills, edited files").set_defaults(handler=cmd_stats)
    digest = commands.add_parser("digest", parents=[common], help="compact transcript of chosen sessions")
    digest.add_argument("ids", nargs="+", metavar="SESSION", help="session id or prefix, optionally agent:prefix")
    digest.set_defaults(handler=cmd_digest)
    commands.add_parser("inventory", parents=[common],
                        help="existing skills and instruction files").set_defaults(handler=cmd_inventory)
    args = parser.parse_args(argv)
    if not args.all and args.project is None:
        args.project = "."
    print(args.handler(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
