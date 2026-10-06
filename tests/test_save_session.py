"""Tests for claude-session-saver. Run with:  python -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest

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
]


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

    def test_skips_meta_and_sidechain(self):
        joined = json.dumps(self.turns)
        self.assertNotIn("meta entry", joined)
        self.assertNotIn("subagent chatter", joined)


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
            tr = os.path.join(d, "t.jsonl")
            with open(tr, "w", encoding="utf-8") as f:
                f.write("\n".join(SAMPLE[:2]))
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

    def test_missing_transcript_is_a_no_op(self):
        self.assertIsNone(ss.save({"transcript_path": "does-not-exist.jsonl"}))


if __name__ == "__main__":
    unittest.main()
