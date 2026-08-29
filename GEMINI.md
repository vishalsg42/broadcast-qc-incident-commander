# GEMINI.md

Project instructions live in **[AGENTS.md](./AGENTS.md)** - read that file first.

It is the single source of truth for architecture rules, conventions, domain
facts, and the traps already paid for. This file exists only so Gemini CLI and
Gemini Code Assist pick the instructions up; duplicating the content here would
guarantee the two copies drift.

## One rule worth repeating here

**Do not use em dashes.** Not in code comments, docstrings, commit messages, UI
copy, or prose. Use a spaced hyphen, a comma, a colon, or two sentences.

It is repeated in this file rather than left to AGENTS.md alone because it
applies to every line of output, including replies that never touch a file, and
a rule that only lives one link away is a rule that gets missed.
