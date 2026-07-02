---
name: memory-recall
description: Consult the project's long-term memory before an expensive code search or when resuming work. Fetches distilled facts via the ltm-memory `recall` tool with a calibrated confidence verdict, then applies a memory-first stop rule — trust strong recall and skip the wider search, widen only when recall is weak or empty. Use when starting or resuming a task, when the user asks "what do we know / what did we decide / where is X / did we already do this", before a broad Grep/Glob/Task sweep of unfamiliar code, or when a search keeps missing. Do NOT use for trivial single-file lookups you can answer directly.
license: MIT
metadata:
  author: Lawrence Eldridge
  version: 0.5.0
  mcp-server: ltm-memory
---

# Memory Recall

Long-term memory is cheaper than searching. Before scanning files, ask what you
already know. This skill wraps the `recall` tool from the **ltm-memory** MCP
server with a confidence-gated stop rule so recall replaces expensive searches
when it can, and defers to them when it can't.

## Instructions

### Step 1: Recall before you search

Call the `recall` tool with your search intent as `query` (natural language is
fine — it is a semantic lookup, not a keyword grep). Optionally pass `project`
to target a different project than the current one; use `list_projects` to see
labels.

```
recall(query="how is auth handled between the two zones")
```

The result is JSON: `facts` (highest-scoring first), a `confidence` (0–1), a
`verdict`, and a one-line `guidance`.

### Step 2: Follow the verdict — the stop rule

The `verdict` decides whether you still need a wider search:

- **`ok`** — Strong recall. Trust these facts and act on them. Do **not** launch
  a broad Grep/Glob/Task search to re-derive what memory already told you. At
  most, open one or two specific files the facts point at to confirm.
- **`low_confidence`** — Treat the facts as hints only. Use them to *narrow* your
  search (better keywords, the right directory), then run it.
- **`no_memory`** — Nothing is stored for this query. Do **not** assume prior
  context or claim the project "already does" something. Proceed with a normal
  search.

### Step 3: Report honestly

When you act on recalled facts, say so briefly ("from memory: …"). When recall
was empty or weak and you fell back to searching, don't present the search
result as if it came from memory.

## Examples

**Resuming work.** User: "let's carry on with the ingestion refactor."
→ `recall(query="ingestion refactor status and decisions")`. Verdict `ok`:
summarise the recalled decisions and continue — no repo sweep needed.

**Direct memory question.** User: "what did we decide about the auth headers?"
→ `recall(query="auth header decision")`. Verdict `ok`: answer from the facts.
Verdict `no_memory`: say it isn't in memory rather than guessing.

**Unfamiliar-area search.** About to Grep the whole repo for "risk scoring".
→ `recall(query="risk scoring implementation")` first. `low_confidence` with a
fact naming a module → Grep that module, not the whole tree.

**Should NOT use.** "Show me line 40 of config.py." Just read the file.

## Troubleshooting

**`recall` tool not available.** The ltm-memory MCP server isn't connected.
Verify the ltm plugin is installed and Claude Code was restarted after install;
the server is declared in the plugin's `.mcp.json`. Until it's up, fall back to
normal searching — memory is an optimisation, never a hard dependency.

**Every query returns `no_memory`.** The store is genuinely empty for this
project (memory is captured on session Stop/End, so a brand-new project has
none) — or you're querying the wrong project. Run `list_projects` to check
labels and fact counts, and pass the right `project`.

**Recall confidence feels too strict or too loose.** The `ok` threshold is the
`recall_min_confidence` plugin option (default 0.35). It is not a per-call knob.
