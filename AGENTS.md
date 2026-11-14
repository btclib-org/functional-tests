# Repository instructions

Follow [CONTRIBUTING.md](./CONTRIBUTING.md) for issues, pull requests,
and local gates. Use [README.md](./README.md) for the
project overview, and [REPOSITORY.md](./REPOSITORY.md) before changing
workflows or settings.
See the [organization security policy](https://github.com/btclib-org/.github/blob/main/SECURITY.md)
for vulnerability reporting.

When an agent works here, a separate worktree per task keeps the
contributor's own checkout clean: hooks and agents change files where
they run. For example, `git worktree add ../<name> origin/main -b <branch>`,
and `git worktree remove ../<name>` when done.

Choose the least capable model that can reliably complete the task: use
a fast model for straightforward execution, a reasoning-focused model
for multi-step work, and the most capable available model for difficult
or ambiguous problems. Increase capability if progress stalls.

Use [TF2.md](./TF2.md) for the ledger of Bitcoin Core test-framework
coverage, and its *Citing the framework from btclib and from tf2* section
for how a framework file is cited. `tests/tf2_ledger_test.py` refuses a
stated count in `TF2.md`; its docstring says why.

## Code Review Rules

Review against [REVIEWING.md](./REVIEWING.md). Its *The verdict* says
which review opens with a verdict line and what every other review
posts.

The heading is the one Codex's GitHub review asks for
([Customize what Codex reviews](https://learn.chatgpt.com/docs/third-party/github#customize-what-codex-reviews)).
