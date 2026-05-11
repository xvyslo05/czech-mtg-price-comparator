# Project rules for Claude Code

## PRs must update README.md

Every pull request that changes user-facing behaviour (new tool, new tool parameter, new env var, new strategy / flag, removed feature, changed default, changed response shape) MUST also update `README.md` in the same PR. This includes:

- New / changed entries in the **Configuration reference** table when an env var is added or removed.
- New / changed bullets in **What this is** and **What you can ask Claude** when a tool's behaviour changes.
- The **How it works under the hood** section when the optimizer / aggregator / adapter pipeline gains a new mode or constraint.
- The **Limitations** section when something previously listed as a limitation is addressed (or a new limitation is introduced).
- A new example in **What you can ask Claude** when a new strategy or significant feature ships.

Internal-only changes (refactors, test additions, CI workflow tweaks, dependency bumps with no behaviour change) do NOT require a README update — but if you're unsure whether a change is user-facing, default to updating the README.

Before opening a PR, verify the README diff in your branch is non-empty when the change is user-facing. If it isn't, add the documentation before pushing.
