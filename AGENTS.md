## Local branch requirement

Every agent working in this repository, including delegated agents, must inspect the Git branch and working-tree status before editing. When starting from `main`, create a task-specific local branch from `main` in this checkout before making changes. Do all task work on that local branch; never edit project files directly on `main` or run the task in Codex Cloud. If already on a local task branch for the same task, continue on it. If branch creation is blocked, stop before editing and report the reason.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
