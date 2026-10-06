# Changelog

## 1.1.1 (2026-10-06)
- **Fix:** notes are always saved under the project root. Before, if Claude changed into a subfolder (`cd src`), the hook used that folder and scattered notes into nested `Journal/` folders, even inside other git repos. Found on my own vault: one stray note landed in a website repo's working tree (it was caught before any commit).
- The project root (`$CLAUDE_PROJECT_DIR`) now takes priority over the hook's `cwd`. New regression test (25 tests).
- Tests clear `CLAUDE_PROJECT_DIR` so running them inside a Claude Code session can't write into the real project.

## 1.1.0 (2026-10-05)
- **`install --portable`:** copies the script into the project (backing up any older copy) and uses `$CLAUDE_PROJECT_DIR`, so settings work on every machine a project is synced or cloned to. Found while installing on a OneDrive-synced vault.
- **`install --footer`:** sets the backlink line under each note title.
- `uninstall` also cleans up the footer setting. 24 unit tests.

## 1.0.0 (2026-10-05)
- **One-command setup:** `claude-session-saver install` adds the hook to `.claude/settings.json` (per project or `--global`). It merges safely, keeps a timestamped backup, and is safe to run twice. `uninstall` removes only this hook.
- **Backfill:** `claude-session-saver backfill` saves notes for all past sessions of a project.
- **Secret redaction:** API keys, tokens, private keys and `password=`-style values are replaced with `[REDACTED]` before anything is written (on by default; `SESSION_SAVER_REDACT=0` turns it off).
- Installable with `pipx` / `pip`; still works as a single file with no dependencies.
- Hook mode never crashes the session: errors go to stderr and it exits 0.
- 22 unit tests (24 as of 1.1.0); CI on Windows, macOS and Linux (Python 3.9–3.13).

## 0.1.0 (2026-10-05)
- First public version: one Markdown note per session, privacy filtering, atomic writes, Obsidian frontmatter.
