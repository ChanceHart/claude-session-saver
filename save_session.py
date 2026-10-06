"""claude-session-saver: save every Claude Code session as a Markdown note.

Runs as a Claude Code **Stop hook** (after every Claude reply). It reads the hook
payload from stdin, loads the session transcript (JSONL), and rewrites one
Markdown note per session.

Privacy by design:
  * only your messages and Claude's replies are saved; tool output, file
    contents and system messages are left out (tools are listed by name only)
  * common secrets (API keys, tokens, private keys, "password=..." values) are
    replaced with [REDACTED] before anything is written

Commands:
  claude-session-saver install [--global] [--project PATH] [--dir DIR]
  claude-session-saver uninstall [--global] [--project PATH]
  claude-session-saver backfill [--project PATH] [--dir DIR]
  claude-session-saver hook        (what Claude Code runs; reads JSON on stdin)

Configuration (environment variables, all optional):
  SESSION_SAVER_DIR     Output folder. Relative paths resolve against the
                        project folder. Default: Journal/Sessions/Transcripts
  SESSION_SAVER_FOOTER  A line under each note's title, e.g. "See also: [[Index]]"
  SESSION_SAVER_REDACT  Set to 0 to turn off secret redaction (default: on)
  CLAUDE_CONFIG_DIR     Claude Code's config folder (default: ~/.claude)

No dependencies. Python 3.9+. Windows, macOS and Linux.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
from typing import Iterable

__version__ = "1.0.0"

DEFAULT_DIR = os.path.join("Journal", "Sessions", "Transcripts")
HOOK_MARKERS = ("save_session.py", "claude-session-saver")

# System-injected blocks that should never land in a note.
STRIP = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)

# Common secret formats. Matches are replaced with [REDACTED].
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),                 # Anthropic
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),           # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS access key ID
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                   # Google API key
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),              # Slack
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),      # Stripe
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
]
# key=value / key: value secrets: keep the key name, hide the value.
SECRET_ASSIGN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
    r"(\s*[:=]\s*)(\"[^\"]+\"|'[^']+'|[^\s,;]+)")
REDACTED = "[REDACTED]"


# ---------------------------------------------------------------- parsing ---

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


def redact(text: str) -> str:
    """Replace common secrets with [REDACTED]."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)


def redaction_enabled() -> bool:
    return os.environ.get("SESSION_SAVER_REDACT", "1").strip().lower() not in ("0", "false", "no", "off")


def parse_transcript(lines: Iterable[str], *, scrub: bool = True) -> tuple[list[dict], str | None]:
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
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in ("user", "assistant") or entry.get("isMeta") or entry.get("isSidechain"):
            continue
        started = started or entry.get("timestamp")
        text, tools = text_of((entry.get("message") or {}).get("content"))
        text = STRIP.sub("", text).strip()
        if scrub:
            text = redact(text)
        role = entry["type"]
        if role == "user" and not text:
            continue  # tool results
        if turns and turns[-1]["role"] == role == "assistant":
            turns[-1]["text"] = "\n\n".join(t for t in (turns[-1]["text"], text) if t)
            turns[-1]["tools"] += tools
        else:
            turns.append({"role": role, "text": text, "tools": tools, "ts": entry.get("timestamp")})
    return turns, started


# -------------------------------------------------------------- rendering ---

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
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------ saving ---

def output_dir(payload: dict) -> str:
    base = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    target = os.path.expanduser(os.environ.get("SESSION_SAVER_DIR", DEFAULT_DIR))
    return target if os.path.isabs(target) else os.path.join(base, target)


def save(payload: dict) -> str | None:
    """Save the session described by a Stop-hook payload. Returns the note path."""
    path = payload.get("transcript_path")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        turns, started = parse_transcript(f, scrub=redaction_enabled())
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


# ------------------------------------------------------- install / settings ---

def claude_home() -> str:
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude"))


def settings_path(project: str | None, global_: bool) -> str:
    if global_:
        return os.path.join(claude_home(), "settings.json")
    return os.path.join(os.path.abspath(project or os.getcwd()), ".claude", "settings.json")


def hook_command() -> str:
    """The command Claude Code should run: this Python + this file, absolute paths."""
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" hook'


def _is_ours(hook: dict) -> bool:
    return any(m in str(hook.get("command", "")) for m in HOOK_MARKERS)


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    data = json.loads(text) if text else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write_settings(path: str, data: dict) -> str | None:
    """Back up the existing file, then write. Returns the backup path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = None
    if os.path.exists(path):
        backup = path + ".bak-" + _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(path, backup)
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    return backup


def install(project: str | None = None, global_: bool = False, notes_dir: str | None = None,
            dry_run: bool = False) -> dict:
    """Add (or update) the Stop hook in settings.json. Safe to run more than once."""
    path = settings_path(project, global_)
    data = _load(path)
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    ours = {"type": "command", "command": hook_command(), "timeout": 60, "async": True}

    replaced = False
    for group in stop:
        hooks = group.get("hooks", []) if isinstance(group, dict) else []
        for i, h in enumerate(hooks):
            if isinstance(h, dict) and _is_ours(h):
                hooks[i] = ours
                replaced = True
    if not replaced:
        stop.append({"hooks": [ours]})
    if notes_dir:
        data.setdefault("env", {})["SESSION_SAVER_DIR"] = notes_dir

    backup = None if dry_run else _write_settings(path, data)
    return {"settings": path, "backup": backup, "updated": replaced, "data": data}


def uninstall(project: str | None = None, global_: bool = False) -> dict:
    path = settings_path(project, global_)
    data = _load(path)
    removed = 0
    groups = data.get("hooks", {}).get("Stop", [])
    for group in list(groups):
        hooks = group.get("hooks", []) if isinstance(group, dict) else []
        keep = [h for h in hooks if not (isinstance(h, dict) and _is_ours(h))]
        removed += len(hooks) - len(keep)
        if keep:
            group["hooks"] = keep
        else:
            groups.remove(group)
    if "hooks" in data and not data["hooks"].get("Stop"):
        data["hooks"].pop("Stop", None)
        if not data["hooks"]:
            data.pop("hooks")
    if data.get("env", {}).pop("SESSION_SAVER_DIR", None) is not None and not data["env"]:
        data.pop("env")
    backup = _write_settings(path, data) if removed else None
    return {"settings": path, "backup": backup, "removed": removed}


# ---------------------------------------------------------------- backfill ---

def encode_project(path: str) -> str:
    """Claude Code's folder name for a project: every non-alphanumeric char becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def backfill(project: str | None = None) -> list[str]:
    """Save notes for every past session of a project. Returns the note paths."""
    project = os.path.abspath(project or os.getcwd())
    folder = os.path.join(claude_home(), "projects", encode_project(project))
    if not os.path.isdir(folder):
        return []
    saved = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".jsonl") or name.startswith("agent-"):
            continue
        note = save({"transcript_path": os.path.join(folder, name),
                     "session_id": name[:-len(".jsonl")], "cwd": project})
        if note:
            saved.append(note)
    return saved


# --------------------------------------------------------------------- CLI ---

def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-session-saver",
        description="Save every Claude Code session as a Markdown note.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("hook", help="run as the Stop hook (reads the hook JSON on stdin)")

    p = sub.add_parser("install", help="add the hook to Claude Code's settings.json")
    p.add_argument("--global", dest="global_", action="store_true", help="install for every project (~/.claude)")
    p.add_argument("--project", help="project folder (default: current folder)")
    p.add_argument("--dir", dest="notes_dir", help="notes folder (default: Journal/Sessions/Transcripts)")
    p.add_argument("--dry-run", action="store_true", help="show the result without writing")

    u = sub.add_parser("uninstall", help="remove the hook from settings.json")
    u.add_argument("--global", dest="global_", action="store_true")
    u.add_argument("--project")

    b = sub.add_parser("backfill", help="save notes for all past sessions of a project")
    b.add_argument("--project", help="project folder (default: current folder)")
    b.add_argument("--dir", dest="notes_dir", help="notes folder (overrides SESSION_SAVER_DIR)")

    args = parser.parse_args(argv)

    if args.cmd in (None, "hook"):
        if args.cmd is None and sys.stdin.isatty():
            parser.print_help()
            return 0
        try:
            save(json.load(sys.stdin))
        except Exception as exc:  # a hook must never break the user's session
            print(f"claude-session-saver: {exc}", file=sys.stderr)
        return 0

    if args.cmd == "install":
        r = install(args.project, args.global_, args.notes_dir, args.dry_run)
        if args.dry_run:
            print(json.dumps(r["data"], indent=2))
            return 0
        print(f"{'Updated' if r['updated'] else 'Installed'} the hook in {r['settings']}")
        if r["backup"]:
            print(f"Backup of the previous settings: {r['backup']}")
        print("Notes will be saved to:", args.notes_dir or os.environ.get("SESSION_SAVER_DIR", DEFAULT_DIR),
              "(relative to each project)" if not os.path.isabs(args.notes_dir or "x") else "")
        return 0

    if args.cmd == "uninstall":
        r = uninstall(args.project, args.global_)
        print(f"Removed {r['removed']} hook(s) from {r['settings']}" if r["removed"] else "No hook found; nothing changed.")
        return 0

    if args.cmd == "backfill":
        if args.notes_dir:
            os.environ["SESSION_SAVER_DIR"] = args.notes_dir
        notes = backfill(args.project)
        print(f"Saved {len(notes)} session note(s)." if notes else "No past sessions found for this project.")
        return 0
    return 1


def main() -> None:  # kept for backwards compatibility with the original hook
    sys.exit(cli(sys.argv[1:] or ["hook"]))


if __name__ == "__main__":
    main()
