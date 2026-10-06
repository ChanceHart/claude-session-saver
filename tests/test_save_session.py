"""Tests for claude-session-saver. Run with:  python -m unittest discover tests -v"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import save_session as ss  # noqa: E402


def line(**kw):
    return json.dumps(kw)


SAMPLE = [
    line(type="user", timestamp="2026-10-05T14:02:00Z",
         message={"content": "Help me fix the login bug<system-reminder>secret context</system-reminder>"}),
    line(type="assistant", message={"content": [
        {"type": "text", "text": "Let me look at the auth module."},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "auth.py"}}]}),
    # Tool result: must never be saved.
    line(type="user", message={"content": [{"type": "tool_result", "content": "FAKE_SECRET_FOR_TESTS_ONLY"}]}),
    line(type="assistant", message={"content": [{"type": "text", "text": "Found it: the token check runs too early."}]}),
    line(type="user", isMeta=True, message={"content": "meta entry"}),
    line(type="assistant", isSidechain=True, message={"content": [{"type": "text", "text": "subagent chatter"}]}),
    "{ this line is corrupt",
    "[1, 2, 3]",
]


def write_transcript(folder, name, lines):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.turns, self.started = ss.parse_transcript(SAMPLE)

    def test_keeps_only_the_conversation(self):
        self.assertEqual([t["role"] for t in self.turns], ["user", "assistant"])
        self.assertEqual(self.started, "2026-10-05T14:02:00Z")

    def test_strips_system_blocks(self):
        self.assertEqual(self.turns[0]["text"], "Help me fix the login bug")

    def test_never_saves_tool_output(self):
        joined = json.dumps(self.turns)
        self.assertNotIn("FAKE_SECRET_FOR_TESTS_ONLY", joined)
        self.assertNotIn("secret context", joined)

    def test_merges_assistant_messages_and_lists_tools_by_name(self):
        a = self.turns[1]
        self.assertIn("auth module", a["text"])
        self.assertIn("token check", a["text"])
        self.assertEqual(a["tools"], ["Read"])

    def test_skips_meta_sidechain_and_bad_lines(self):
        joined = json.dumps(self.turns)
        self.assertNotIn("meta entry", joined)
        self.assertNotIn("subagent chatter", joined)


class RedactionTests(unittest.TestCase):
    # Fake values built at runtime so no secret-looking string sits in the repo.
    FAKES = {
        "anthropic": "sk-ant-" + "a1" * 15,
        "github": "ghp_" + "X" * 36,
        "aws": "AKIA" + "ABCDEFGHIJ123456",
        "google": "AIza" + "b" * 35,
        "slack": "xoxb-" + "1234567890-abc",
        "jwt": "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    }

    def test_redacts_common_token_formats(self):
        for kind, value in self.FAKES.items():
            with self.subTest(kind=kind):
                out = ss.redact(f"my key is {value} ok")
                self.assertNotIn(value, out)
                self.assertIn(ss.REDACTED, out)

    def test_redacts_private_keys(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(ss.redact(pem), ss.REDACTED)

    def test_keeps_key_names_hides_values(self):
        self.assertEqual(ss.redact("password=hunter2"), "password=[REDACTED]")
        self.assertEqual(ss.redact('api_key: "abc 123"'), "api_key: [REDACTED]")

    def test_leaves_normal_text_alone(self):
        text = "The password reset page needs a better error message."
        self.assertEqual(ss.redact(text), text)

    def test_redaction_applies_when_parsing_and_can_be_disabled(self):
        lines = [line(type="user", message={"content": "token=abc123secret"})]
        turns, _ = ss.parse_transcript(lines)
        self.assertEqual(turns[0]["text"], "token=abc123secret")  # 'token' alone is not a secret key name
        lines = [line(type="user", message={"content": "auth_token=abc123secret"})]
        self.assertEqual(ss.parse_transcript(lines)[0][0]["text"], "auth_token=[REDACTED]")
        self.assertEqual(ss.parse_transcript(lines, scrub=False)[0][0]["text"], "auth_token=abc123secret")
        with mock.patch.dict(os.environ, {"SESSION_SAVER_REDACT": "0"}):
            self.assertFalse(ss.redaction_enabled())


class RenderTests(unittest.TestCase):
    def test_note_has_frontmatter_and_sections(self):
        turns, _ = ss.parse_transcript(SAMPLE)
        note = ss.render(turns, day="2026-10-05", session_id="abc", now="2026-10-05 14:10", footer="See [[Index]]")
        self.assertTrue(note.startswith("---\ntype: session-transcript"))
        self.assertIn("> See [[Index]]", note)
        self.assertIn("### 🧑 **Me**", note)
        self.assertIn("_Tools used: Read_", note)

    def test_title_is_filesystem_safe(self):
        self.assertEqual(ss.note_title([{"role": "user", "text": 'Fix: "a/b\\c" <now>?'}]), "Fix abc now")


class SaveTests(unittest.TestCase):
    def test_writes_one_note_per_session_and_updates_it(self):
        with tempfile.TemporaryDirectory() as d:
            tr = write_transcript(d, "t.jsonl", SAMPLE[:2])
            payload = {"transcript_path": tr, "session_id": "1234abcd-xyz", "cwd": d}
            first = ss.save(payload)
            with open(tr, "a", encoding="utf-8") as f:
                f.write("\n" + SAMPLE[3])
            second = ss.save(payload)
            self.assertEqual(first, second)  # same file, rewritten
            self.assertTrue(first.endswith("(1234abcd).md"))
            with open(second, encoding="utf-8") as f:
                self.assertIn("token check", f.read())
            leftovers = [n for n in os.listdir(os.path.dirname(second)) if n.endswith(".tmp")]
            self.assertEqual(leftovers, [])  # atomic write cleaned up

    def test_custom_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            tr = write_transcript(d, "t.jsonl", SAMPLE[:2])
            with mock.patch.dict(os.environ, {"SESSION_SAVER_DIR": "notes"}):
                note = ss.save({"transcript_path": tr, "session_id": "s1", "cwd": d})
            self.assertEqual(os.path.dirname(note), os.path.join(d, "notes"))

    def test_missing_transcript_is_a_no_op(self):
        self.assertIsNone(ss.save({"transcript_path": "does-not-exist.jsonl"}))


class InstallTests(unittest.TestCase):
    def test_install_merges_backs_up_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings))
            existing = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo other"}]}]}}
            with open(settings, "w", encoding="utf-8") as f:
                json.dump(existing, f)

            r1 = ss.install(project=d, notes_dir="Notes/AI")
            self.assertTrue(r1["backup"] and os.path.exists(r1["backup"]))
            r2 = ss.install(project=d, notes_dir="Notes/AI")
            self.assertTrue(r2["updated"])

            with open(settings, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["model"], "opus")  # other settings kept
            commands = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
            self.assertIn("echo other", commands)  # other hooks kept
            self.assertEqual(sum("save_session.py" in c for c in commands), 1)  # no duplicates
            self.assertEqual(data["env"]["SESSION_SAVER_DIR"], "Notes/AI")

    def test_uninstall_removes_only_our_hook(self):
        with tempfile.TemporaryDirectory() as d:
            ss.install(project=d, notes_dir="x")
            settings = ss.settings_path(d, False)
            with open(settings, encoding="utf-8") as f:
                data = json.load(f)
            data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo keep"}]})
            with open(settings, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.assertEqual(ss.uninstall(project=d)["removed"], 1)
            with open(settings, encoding="utf-8") as f:
                data = json.load(f)
            commands = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
            self.assertEqual(commands, ["echo keep"])
            self.assertNotIn("env", data)

    def test_portable_install_copies_script_and_uses_project_dir(self):
        with tempfile.TemporaryDirectory() as d:
            hooks = os.path.join(d, ".claude", "hooks")
            os.makedirs(hooks)
            old = os.path.join(hooks, "save_session.py")
            with open(old, "w", encoding="utf-8") as f:
                f.write("# old version")
            r = ss.install(project=d, portable=True, footer="See [[Index]]")
            self.assertTrue(r["script_backup"] and os.path.exists(r["script_backup"]))  # old script kept
            with open(old, encoding="utf-8") as f:
                self.assertIn("__version__", f.read())  # new script copied in
            cmd = r["data"]["hooks"]["Stop"][0]["hooks"][0]["command"]
            self.assertIn("$CLAUDE_PROJECT_DIR/.claude/hooks/save_session.py", cmd)
            self.assertNotIn(d, cmd)  # no machine-specific path
            self.assertEqual(r["data"]["env"]["SESSION_SAVER_FOOTER"], "See [[Index]]")

    def test_portable_global_is_rejected(self):
        with self.assertRaises(ValueError):
            ss.install(global_=True, portable=True, dry_run=True)

    def test_global_install_uses_claude_config_dir(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d}):
            r = ss.install(global_=True)
            self.assertEqual(r["settings"], os.path.join(d, "settings.json"))
            self.assertTrue(os.path.exists(r["settings"]))


class BackfillTests(unittest.TestCase):
    def test_encode_project_matches_claude_code(self):
        self.assertTrue(ss.encode_project("/home/sam/my.vault").endswith("-home-sam-my-vault"))

    def test_backfill_saves_past_sessions_and_skips_agents(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project, \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": home, "SESSION_SAVER_DIR": "notes"}):
            folder = os.path.join(home, "projects", ss.encode_project(project))
            os.makedirs(folder)
            write_transcript(folder, "aaaa1111-0000.jsonl", SAMPLE[:2])
            write_transcript(folder, "bbbb2222-0000.jsonl", SAMPLE[:2])
            write_transcript(folder, "agent-cccc3333.jsonl", SAMPLE[:2])
            notes = ss.backfill(project)
            self.assertEqual(len(notes), 2)
            self.assertTrue(sorted(os.listdir(os.path.join(project, "notes")))[0].endswith("(aaaa1111).md"))


class CliTests(unittest.TestCase):
    def test_version_and_dry_run(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            ss.cli(["--version"])
        self.assertIn(ss.__version__, buf.getvalue())
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(ss.cli(["install", "--project", d, "--dry-run"]), 0)
            self.assertIn("save_session.py", buf.getvalue())
            self.assertFalse(os.path.exists(os.path.join(d, ".claude")))  # dry run wrote nothing

    def test_hook_never_crashes_on_bad_input(self):
        with mock.patch("sys.stdin", io.StringIO("not json")):
            self.assertEqual(ss.cli(["hook"]), 0)


if __name__ == "__main__":
    unittest.main()
