# Receipts: agent memory that knows when it's wrong

**Tagline:** Every memory cites code. Relate's graph decides what is still true.

## Inspiration

Coding agents now carry memory between sessions: `CLAUDE.md`, memory tools, vector stores of learned facts. We kept
hitting the same failure: the agent confidently repeats something that *used to be* true. "Money is stored in integer
cents." Not since last Tuesday's refactor. Plain notes and vector memories store *what* the agent believed, never
*why*, so nothing can tell when the why goes away.

In 1979, Jon Doyle's *Truth Maintenance System* gave AI programs beliefs with justifications: retract a justification
and every belief resting on it falls. That needs a graph of beliefs, evidence and dependencies, living right next to
the agent. Relate made it practical.

## What it does

Receipts is long-term memory for coding agents, exposed as an MCP server, Claude Code hooks and a CLI.

- **No receipt, no memory.** `remember` only accepts a fact that cites real code: a symbol (`money.py::Money`), an
  exact quoted line (`settings.yaml: "tax_rate: 0.08"`), or a file. Citations that point at nothing are rejected.
- **One Relate graph** holds memories, the code (files, symbols, call edges, imports), code changes, and the links
  between them: `CITES`, `CALLS`, `DERIVED_FROM`, `MOVED_TO`, `SUPERSEDES`.
- **Staleness propagates through the graph:**
  - *stale*: the memory's own receipt changed or disappeared;
  - *suspect (upstream drift)*: code the receipt calls, two hops away, changed its signature;
  - *suspect (derived)*: a memory it was built on is no longer good, at any depth.
- **No false alarms.** Hashes are semantic (from the AST, without names or docstrings), so reformatting changes
  nothing, and renamed or moved functions are followed automatically.
- **The agent is told at the right moment:** at session start ("these memories no longer match the code, and here's
  why"), right after it edits code that a memory cites, after a `git pull`, and when it opens a file.
- **It repairs its own memory:** `verify` shows the diff that broke a memory; `reaffirm`, `revise` and `retract` keep
  full history. `impact` shows which memories a change will put at risk *before* the agent makes it.
- **`receipts heal`:** Claude reviews stale memories parent-first and reaffirms, revises or retracts them (structured
  outputs), so memory can maintain itself in CI.
- **A browser viewer** runs Relate compiled to WebAssembly in the tab: the memory graph coloured by status, receipts
  with diffs, and a Cypher console.

We ran it for real. Claude Code onboarded onto a sample repo and saved 7 memories over MCP (it caught that
`Money.times` uses banker's rounding). After a teammate's refactor, a fresh session was told at startup that all 7
were out of date, re-read the code, and revised 6 and reaffirmed 1. That store ships in the repo.

## How we built it

- **Relate** (Python package `relate-db`) is the whole backend: one `.rl` file per repository. Every MCP call and
  hook opens the store under a file lock, runs one transaction and closes it. A complete hook takes about 0.1 s, which
  is why it can run after every tool call.
- The truth-maintenance rules are three Cypher statements using quantified path patterns:
  `(:Fact)-[:CITES]->(:Symbol)-[:CALLS]->{1,2}(:Symbol)<-[:ON]-(:Change {kind:'signature'})` and
  `(:Fact)-[:DERIVED_FROM]->+(:Fact)`. `recall` combines Relate's full-text index with graph expansion over
  `CALLS`, `IMPORTS` and `DERIVED_FROM`.
- A Python AST scanner (plus a regex scanner for JS/TS) builds the code graph incrementally; only changed files are
  re-parsed.
- The MCP server is hand-rolled JSON-RPC over stdio (no SDK), like Relate's own.
- The viewer replays an exported Cypher script into `@relate/relate-wasm` and renders it with Cytoscape.
- `heal` uses the Claude API (`claude-opus-5-5`, JSON-schema structured outputs, server-side refusal fallbacks).
- 13 tests cover hashing, quote boundaries, every propagation path, repair, the MCP protocol, hooks, heal (with a fake
  client) and the export round-trip.

## Challenges we ran into

- **A Relate engine bug.** In relate-db 1.0.2, deleting a node or relationship created earlier in the same open
  transaction panics (`update counters are non-negative`). We isolated a minimal repro and work around it by
  committing before any delete phase.
- **Not crying wolf.** A memory system that flags everything is useless. Semantic hashing, rename following, and
  token-boundary quote matching (so `tax_rate: 0.08` doesn't match inside `0.0825`) took several iterations.
- **Agents improvise.** In the live run the agent called `recall` with `question` instead of `query`; the server now
  accepts the obvious aliases.

## Accomplishments that we're proud of

- The graph does real reasoning: four different outcomes (stale, drift-suspect, derived-suspect, rename-followed)
  from one refactor, each traceable to a path in the graph.
- A real agent used it end to end without hand-holding, and fixed its own memory.
- Zero-install judging: `uvx … receipts demo` runs in under a second, and the viewer runs Relate in the browser.

## What we learned

Embedded matters. Because Relate is a library, the memory graph can sit inside every hook and tool call instead of
behind a service. Connected context is what lets an agent say "I'm not sure that's still true" instead of being
confidently wrong.

## What's next

- Quote receipts that follow text moved across files; more languages through tree-sitter.
- `receipts heal` as a GitHub Action that opens a PR updating memory after each merge.
- Sync with Neo4j Aura (`relate push`) so an organisation can query memory across repositories.

## Built with

relate · neo4j · cypher · python · webassembly · mcp · claude-code · claude-api · cytoscape.js
