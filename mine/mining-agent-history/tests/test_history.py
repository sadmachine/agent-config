import contextlib
import io
import json
import os
import shutil
import sqlite3
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

    def test_command_prefixes_skip_multiline_strings(self):
        command = "python3 -c \"\nimport json\ntoken = 1\n\" && git commit -m 'subject\n\nbody' && npm test"
        self.assertEqual(history.command_prefixes(command), ["python3", "git commit", "npm test"])

    def test_correction_tags(self):
        for text in ["No, use the release branch", "don't touch vendor", "I told you to use pnpm", "that’s not right"]:
            self.assertTrue(history.is_correction(text), text)
        for text in ["Add a migration for the users table", "Nothing else needed"]:
            self.assertFalse(history.is_correction(text), text)


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

    def test_project_skill_wins_name_clash(self):
        write_file(os.path.join(self.project, ".agents", "skills", "alpha", "SKILL.md"),
                   "---\nname: alpha\ndescription: Project alpha\n---\n")
        out = self.inventory("--project", self.project)
        self.assertIn("| alpha | Project alpha |", out)
        self.assertNotIn("Line one line two", out)


def make_codex_db(codex_dir, threads, items, turns):
    """Minimal copies of Codex's state_N.sqlite and thread_history_N.sqlite tables the reader uses."""
    os.makedirs(codex_dir, exist_ok=True)
    with sqlite3.connect(os.path.join(codex_dir, "state_5.sqlite")) as state:
        state.execute("create table threads (id text, rollout_path text, cwd text, git_origin_url text,"
                      " git_branch text, created_at integer, source text)")
        state.executemany("insert into threads values (?, ?, ?, ?, ?, ?, ?)", threads)
    with sqlite3.connect(os.path.join(codex_dir, "thread_history_1.sqlite")) as history_db:
        history_db.execute("create table thread_items (thread_id text, turn_id text, item_id text,"
                           " rollout_ordinal integer, created_at_ms integer, item_json text, item_type text)")
        history_db.execute("create table thread_turns (thread_id text, turn_id text, rollout_ordinal integer,"
                           " status text)")
        history_db.executemany("insert into thread_items values (?, ?, ?, ?, 0, ?, ?)",
                               [(t, turn, f"i{n}", n, json.dumps(item), item["type"])
                                for n, (t, turn, item) in enumerate(items)])
        history_db.executemany("insert into thread_turns values (?, ?, ?, ?)", turns)


def user_item(text, *parts):
    return {"type": "userMessage", "content": [{"type": "text", "text": text}, *parts]}


class CodexDatabase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.codex = os.path.join(self.tmp, "codex")
        live = os.path.join(self.codex, "sessions", "2026", "01", "05", os.path.basename(CODEX_EVENTS))
        os.makedirs(os.path.dirname(live))
        shutil.copy(CODEX_EVENTS, live)
        make_codex_db(self.codex, [
            ("t-gone", "/deleted/rollout-gone.jsonl", "/fixture/app", "git@github.com:Acme/App.git", "main",
             1788000000, "cli"),
            ("0188bbbb-0002-7000-8000-000000000000", live, "/fixture/app", None, None, 1767600000, "vscode"),
            ("t-sub", "/deleted/rollout-sub.jsonl", "/fixture/app", None, None, 1788000000,
             '{"subagent":{"other":"guardian"}}'),
        ], [
            ("t-gone", "u1", user_item("deploy the app to staging now",
                                       {"type": "skill", "name": "handoff", "path": "/s/handoff/SKILL.md"})),
            ("t-gone", "u1", {"type": "commandExecution", "command": "rtk git push origin dev"}),
            ("t-gone", "u1", {"type": "fileChange", "changes": [{"path": "/fixture/app/src/app.py"}]}),
            ("t-gone", "u1", {"type": "agentMessage", "text": "Deploying now."}),
            ("t-gone", "u2", user_item("use the release branch")),
            ("0188bbbb-0002-7000-8000-000000000000", "v1", user_item("Add a migration for the orders table")),
            ("t-sub", "s1", user_item("Assess the exact planned action")),
        ], [("t-gone", "u1", 0, "interrupted"), ("t-gone", "u2", 1, "completed")])
        write_file(os.path.join(self.codex, "state_4.sqlite"), "not a database")
        write_file(os.path.join(self.codex, "archived_sessions", "2026", "02", "01", "rollout-archived.jsonl"),
                   json.dumps({"type": "session_meta", "payload": {"id": "archived-1", "cwd": "/fixture/app",
                                                                  "timestamp": "2026-02-01T00:00:00Z"}}) + "\n"
                   + json.dumps({"type": "event_msg", "payload": {"type": "user_message",
                                                                 "message": "archived prompt here"}}) + "\n")

    def discover(self):
        out = run("discover", "--global", "--claude-dir", MISSING, "--codex-dir", self.codex, "--paseo-file", MISSING)
        return {line.split("\t")[1]: line.split("\t") for line in out.splitlines()[1:] if not line.startswith("#")}, out

    def test_reads_deleted_threads_once_and_archived_sessions(self):
        rows, out = self.discover()
        self.assertEqual(sorted(rows), ["0188bbbb-0002-7000-8000-000000000000", "archived-1", "t-gone"])
        self.assertEqual(rows["t-gone"][3], "github.com/acme/app")
        self.assertEqual(rows["t-gone"][6][:10], "2026-08-29")
        self.assertEqual(rows["t-gone"][7], "2")
        self.assertIn("# skipped 1 subagent sessions", out)

    def test_database_items_become_turns(self):
        out = run("digest", "t-gone", "--global", "--claude-dir", MISSING, "--codex-dir", self.codex,
                  "--paseo-file", MISSING)
        for expected in ("t1 skill: $handoff", "prompt: deploy the app to staging now", "command: git push",
                         "edit: /fixture/app/src/app.py", "interrupt: [turn interrupted]",
                         "prompt [correction]: use the release branch", "(reply before) Deploying now."):
            self.assertIn(expected, out)

    def test_unreadable_database_warns(self):
        write_file(os.path.join(self.codex, "thread_history_1.sqlite"), "garbage")
        rows, out = self.discover()
        self.assertIn("warning: could not read Codex history database", out)
        self.assertEqual(sorted(rows), ["0188bbbb-0002-7000-8000-000000000000", "archived-1"])


if __name__ == "__main__":
    unittest.main()
