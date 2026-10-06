# Security

## What this tool saves
- **Saved:** the text of your messages and Claude's replies, the session date and ID, and tool **names**.
- **Never saved:** tool output, file contents, command results, system and meta messages, and sub-agent chatter.
- **Redacted by default:** common secret formats (Anthropic/OpenAI-style keys, GitHub tokens, AWS key IDs, Google API keys, Slack and Stripe tokens, JWTs, PEM private keys) and values written as `password=…`, `api_key: …`, `client_secret=…` and similar.

Redaction is a safety net, not a guarantee: a secret typed in an unusual format could still be saved. Don't paste secrets into chats, and keep your notes folder private.

## Where it runs
Everything stays on your machine. The tool makes **no network requests** and has no dependencies.

## Reporting a problem
If you find a way for sensitive data to end up in a note, please open an issue **without** including the secret itself, or contact me through [chancehart.github.io](https://chancehart.github.io).
