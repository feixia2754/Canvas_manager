Review the current git diff (staged + unstaged) and any untracked files to identify new conventions, modules, commands, or dependencies introduced since the last commit.

1. Run `git diff HEAD` and `git status --short` to see all changes and new files.
2. Read the current `CLAUDE.md`.
3. Identify anything in the diff worth documenting: new CLI commands, new modules, changed architectural rules, new dependencies, updated conventions.
4. Propose the minimal edits to `CLAUDE.md` that reflect these changes — no rewrites, just targeted additions or updates.
5. Show the proposed changes as a unified diff (--- / +++ format) and wait for my approval before writing anything.
