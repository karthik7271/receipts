# Devpost submission text

**Project name** (49/60): Receipts: Agent Memory That Knows When It's Wrong

**Elevator pitch** (172/200): Coding agents remember facts about your code, then the code changes and the memory doesn't. Receipts makes every memory cite code, and a Relate graph flags what went stale.

**Links**
- Code: https://github.com/karthik7271/receipts
- Live memory graph (Relate in your browser): https://karthik7271.github.io/receipts/ (and the Vercel URL once deployed)
- Try it: `uvx --from git+https://github.com/karthik7271/receipts receipts demo`

---

## Inspiration

Coding agents now carry memory between sessions: `CLAUDE.md`, memory tools, vector stores of learned facts. We kept
seeing the same failure: the agent confidently repeats something that *used to be* true. "Money is stored in integer
cents." Not since last week's refactor. Notes and vector memories store *what* the agent believed, never *why*, so
nothing notices when the why goes away.

In 1979, Jon Doyle's *Truth Maintenance System* gave AI programs beliefs with justifications: withdraw a justification
and every belief resting on it falls. That needs a graph of beliefs, evidence and dependencies living right next to
the agent. Relate made it practical.

## What it does

Receipts is long-term memory for coding agents, as an MCP server, Claude Code hooks and a CLI.

- **No receipt, no memory.** A memory must cite real code: a symbol, an exact quoted line, or a file. Citations that
  point at nothing are rejected.
- **One Relate graph** holds the memories, the code (symbols, calls, imports), every code change, and which memories
  build on which.
- **When code changes, the graph decides what's still true:**
  - *stale*: the cited code's behaviour changed or it disappeared;
  - *suspect*: code it depends on, two calls away, changed its contract, or a memory it builds on went bad, at any
    depth;
  - *fresh*: reformatting, comments, docstrings, type-hint-only edits and renames don't trigger alarms.
- **The agent hears it at the right moment:** at session start, right after an edit, after a `git pull`, when it opens
  a file, and before it stops (if it studied code but saved nothing).
- **It repairs itself:** `verify` shows the diff that broke a memory; `reaffirm`, `revise` and `retract` keep full
  history. `receipts heal` lets Claude do this in bulk, for example in CI.
- **A browser viewer** runs Relate as WebAssembly in the tab, with a Cypher console.

**Proven on real code, unprompted.** In a Flask 2.3 checkout, Claude Code answered a question about error handlers
and saved a memory on its own. After upgrading the repo to Flask 3.1, where that function moved and changed, a fresh
session was told the memory was stale before reading anything. It re-checked the code, revised the memory, and
answered the upgrade question. Neither prompt mentioned memory. The two sessions cost $0.07 and $0.22.

**Measured on real release history.** Imagine a memory citing every function in Flask 2.2 → 3.1 and Requests
2.28 → 2.32. Citing whole files would flag 58–100% of them per release, and citing exact text 5–98%. Receipts flags
**4–19%**, while still catching every behaviour change. It ignored hundreds of cosmetic edits and followed 329
renames and moves, including Requests moving its whole package into `src/`.

## How we built it

- **Relate** (`relate-db`) is the entire backend: one embedded `.rl` file per repository. Every hook and tool call
  opens it, runs one transaction and closes it, which is why it can run after every tool call: about 0.1 s per edit
  hook, and about 1 s per session start even on Django (40k symbols).
- **Truth maintenance is a few Cypher statements** with quantified path patterns:
  `(:Fact)-[:CITES]->(:Symbol)-[:CALLS]->{1,2}(:Symbol)<-[:ON]-(:Change)` and `(:Fact)-[:DERIVED_FROM]->+(:Fact)`.
  `recall` combines Relate's full-text index with graph expansion.
- **A Python AST scanner** gives every symbol three fingerprints: behaviour (no names, docstrings or type hints),
  shape (parameters), and canonical type hints (`Optional[X]` equals `X | None`).
- The **MCP server** is hand-rolled JSON-RPC (no SDK). The **viewer** replays the store into `@relate/relate-wasm`.
  `heal` uses the Claude API with structured outputs.
- 20 tests, plus a benchmark that replays real releases through the full pipeline.

## Challenges we ran into

- **Agents don't use memory just because it's there.** In our first unprompted run, Claude answered correctly and
  never touched memory. A once-per-session Stop hook fixed it: the agent now saves what it learned, by itself.
- **Noise.** Our first benchmark showed 72% of Flask 2.3's "changes" were only type-hint rewrites. Behaviour-level
  fingerprints cut flagged memories from 51% to 19% on that release.
- **Scale.** Django's first scan crashed on Relate's 64 MiB per-commit cap, and an unchanged re-sync took 12 s. Now
  scans are written in batches and unchanged files are skipped by size, mtime and hash.
- **A Relate engine bug:** deleting something created in the same open transaction panics. We isolated a minimal
  repro and work around it.

## Accomplishments that we're proud of

- An agent keeping its own memory honest on a real, well-known codebase, without being told to.
- Real numbers from real release history, not just a toy demo.
- Zero-install judging: a one-line `uvx` demo, and a viewer that runs the database in your browser.

## What we learned

Embedded matters. Because Relate is a library, the memory graph lives inside every hook and tool call instead of
behind a service. And connected context is what lets an agent say "I'm not sure that's still true" instead of being
confidently wrong.

## What's next

- Tree-sitter scanners for more languages.
- Quote receipts that follow text across files.
- `receipts heal` as a GitHub Action that opens a PR updating memory after each merge.
- `relate push` to Neo4j Aura, so a team can query memory across repositories.

## Built with

relate · neo4j · cypher · python · webassembly · mcp · claude-code · claude-api · cytoscape.js
