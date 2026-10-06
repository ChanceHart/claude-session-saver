"""claude-session-saver: save every Claude Code session as a Markdown note.

Runs as a Claude Code **Stop hook** (after every Claude reply). It reads the hook
payload from stdin, loads the session transcript (JSONL), and rewrites one
Markdown note per session.

Privacy by design: only your messages and Claude's replies are saved. Tool calls
are listed by name only; tool output, file contents and system messages are
left out.

Configuration (all optional, via environment variables):
    SESSION_SAVER_DIR     Output folder. Relative paths resolve against the
                          project folder. Default: Journal/Sessions/Transcripts
    SESSION_SAVER_FOOTER  A line added under the title of every note, e.g.
                          "See also: [[Current State]]". Default: none.

No dependencies. Python 3.8+.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from typing import Iterable

DEFAULT_DIR = os.path.join("Journal", "Sessions", "Transcripts")

# System-injected blocks that should never land in a note.
STRIP = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)


def text_of(content) -> tuple[str, list[str]]:
    """Return (text, tool_names) from a message's content (string or block list)."""
    if isinstance(content, str):
        return content, []
    parts, tools = [], []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tools.append(block.get("name", "tool"))
    return "\n".join(parts), tools


def parse_transcript(lines: Iterable[str]) -> tuple[list[dict], str | None]:
    """Turn JSONL transcript lines into conversation turns.

    Returns (turns, first_timestamp). Each turn is {role, text, tools, ts}.
    Consecutive assistant messages are merged; tool results (user messages with
    no text) and meta/sidechain entries are skipped.
    """
    turns: list[dict] = []
    started = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # tolerate partial or corrupt lines
        if entry.get("type") not in ("user", "assistant") or entry.get("isMeta") or entry.get("isSidechain"):
            continue
        started = started or entry.get("timestamp")
        text, tools = text_of((entry.get("message") or {}).get("content"))
        text = STRIP.sub("", text).strip()
        role = entry["type"]
        if role == "user" and not text:
            continue  # tool results
        if turns and turns[-1]["role"] == role == "assistant":
            turns[-1]["text"] = "\n\n".join(t for t in (turns[-1]["text"], text) if t)
            turns[-1]["tools"] += tools
        else:
            turns.append({"role": role, "text": text, "tools": tools, "ts": entry.get("timestamp")})
    return turns, started


def note_title(turns: list[dict]) -> str:
    """A filesystem-safe title from the first user message."""
    first = next((t["text"] for t in turns if t["role"] == "user"), "Session")
    title = re.sub(r"[^\w .,'-]", "", first.splitlines()[0] if first else "")[:50].strip()
    return title or "Session"


def render(turns: list[dict], *, day: str, session_id: str, now: str, footer: str = "") -> str:
    """Render turns as an Obsidian-friendly Markdown note with YAML frontmatter."""
    out = [
        "---",
        "type: session-transcript",
        f"created: {day}",
        f"session_id: {session_id}",
        f"updated: {now}",
        "tags: [session, transcript]",
        "---",
        f"# Session transcript: {day}",
        "",
    ]
    if footer:
        out += [f"> {footer}", ""]
    for t in turns:
        out.append("### 🧑 **Me**" if t["role"] == "user" else "### 🤖 **Claude**")
        if t["text"]:
            out.append(t["text"])
        if t["tools"]:
            out.append(f"\n_Tools used: {', '.join(sorted(set(t['tools'])))}_")
        out.append("")
    return "\n".join(out)


def write_atomic(path: str, text: str) -> None:
    """Write to a temp file, then swap it in, so sync tools never see half a note."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def output_dir(payload: dict) -> str:
    base = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    target = os.environ.get("SESSION_SAVER_DIR", DEFAULT_DIR)
    return target if os.path.isabs(target) else os.path.join(base, target)


def save(payload: dict) -> str | None:
    """Save the session described by a Stop-hook payload. Returns the note path."""
    path = payload.get("transcript_path")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        turns, started = parse_transcript(f)
    if not turns:
        return None

    sid_full = payload.get("session_id") or "session"
    sid = sid_full[:8]
    day = (started or _dt.datetime.now().isoformat())[:10]
    out_dir = output_dir(payload)
    os.makedirs(out_dir, exist_ok=True)

    # Keep one file per session even if the first message changes.
    existing = [n for n in os.listdir(out_dir) if n.endswith(f"({sid}).md")]
    name = existing[0] if existing else f"{day} {note_title(turns)} ({sid}).md"
    note = render(turns, day=day, session_id=sid_full,
                  now=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                  footer=os.environ.get("SESSION_SAVER_FOOTER", ""))
    dest = os.path.join(out_dir, name)
    write_atomic(dest, note)
    return dest


def main() -> None:
    save(json.load(sys.stdin))


if __name__ == "__main__":
    main()
