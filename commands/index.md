---
description: Build or update the citegraph index for this project, so the citegraph tools can answer
allowed-tools: Bash(uv run --project *)
---

Index this project with citegraph so its MCP tools (`what_calls`, `find_path` and the rest) have something to answer from.

1. The folder to index is the project root, the directory Claude Code was started in. It must be a git repository, or a folder that holds git repositories.
2. Run this with the Bash tool, with the project root's absolute path in place of PROJECT_ROOT (keep the quotes; it may contain spaces):

   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" --frozen --no-dev --quiet citegraph index "PROJECT_ROOT"
   ```

   The first run sets up citegraph's environment and can take a minute. Later runs re-read only the files that changed, so run it again after pulling or editing.
3. Report what it printed: the repos and files indexed, or the error. If `uv` is not found, tell the user to install uv (https://docs.astral.sh/uv/) and start a new session.
4. The tools pick up the new index without a restart. If `/mcp` shows the citegraph server as failed (its first start can time out while uv sets up), ask the user to reconnect it from `/mcp`; it starts quickly now that the environment exists.
