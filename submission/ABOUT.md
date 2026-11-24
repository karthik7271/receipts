## Inspiration

Coding agents now carry memory between sessions: `CLAUDE.md` files, memory tools, vector stores of learned facts. We kept hitting the same failure: the agent confidently repeats something that *used to be* true. "Money is stored in integer cents." Not since last week's refactor. Every memory system we tried stores *what* the agent believed, never *why*, so nothing notices when the why goes away.

In 1979, Jon Doyle's *Truth Maintenance System* gave AI programs beliefs with justifications: withdraw a justification, and every belief resting on it falls. That needs a graph of beliefs, evidence and dependencies living right next to the agent. Relate, an embedded graph database, made that practical.

## What it does

**Receipts is long-term memory for coding agents in which every memory cites the code that proves it.** It runs as an MCP server, Claude Code hooks and a CLI.

- **No receipt, no memory.** A memory must cite real code: a function, an exact quoted line, or a file. Citations that point at nothing are rejected.
- **One Relate graph** holds the memories, the code (symbols, calls, imports), every code change, and which memories build on which.
- **When code changes, the graph decides what's still true:**
  - **stale:** the cited code's behaviour changed or it disappeared;
  - **suspect:** code it depends on, up to two calls away, changed its contract, or a memory it builds on went bad, at any depth;
  - **fresh:** reformatting, comments, docstrings, type-hint-only edits and renames don't trigger alarms. Renamed functions are followed automatically.
- **The agent hears it at the right moment:** at session start, right after it edits code a memory depends on, after a `git pull`, when it opens a file, and before it stops if it studied code but saved nothing.
- **It repairs its own memory.** `verify` shows the diff that broke a memory. `reaffirm`, `revise` and `retract` keep the full history. `receipts heal` lets Claude do this in bulk, for example in CI.
- **A browser viewer** runs Relate as WebAssembly in the tab, with the memory graph, receipts with diffs, and a Cypher console.

**It works on real code, unprompted.**
1. **Flask 2.3.** We asked Claude Code how Flask picks an error handler. It answered, then saved a non-obvious fact on its own.
2. **The upgrade.** We moved the repo to Flask 3.1, where that function had moved and changed.
3. **A fresh session.** It was told the memory was stale before it read anything. It re-checked the code, revised the memory, and answered the upgrade question.

Neither prompt mentioned memory. The two sessions cost $0.07 and $0.22.

## How we built it

- **Relate, in single-file mode, is the entire backend.** Each repository keeps one `.receipts/memory.rl`. Every hook and tool call opens it, runs one transaction and closes it: about 0.1 s per edit hook, and about 1 s per session start even on Django (4,300 files, 40k symbols). That's why it can run after every tool call, with no server.
- **Truth maintenance is a few Cypher statements using quantified path patterns.** Here is a simplified version of the upstream-drift rule:

```cypher
MATCH (f:Fact)-[c:CITES]->(s:Symbol)-[:CALLS]->{1,2}(t:Symbol)<-[:ON]-(ch:Change)
WHERE ch.kind IN ['signature', 'types'] AND ch.tick > c.tick
SET f.status = 'suspect'
```

  Derived suspicion is `(:Fact)-[:DERIVED_FROM]->+(:Fact)`, and `recall` combines Relate's full-text index with graph expansion over calls, imports and derivations.
- **A Python AST scanner gives every symbol three fingerprints:**
  - **behaviour:** ignores names, docstrings and type hints;
  - **shape:** the parameters;
  - **canonical type hints:** `Optional[X]` counts the same as `X | None`.

  Hint-only edits never make a memory stale, and moved code keeps its behaviour fingerprint.
- **The rest of the stack:**
  - a hand-rolled MCP server (JSON-RPC over stdio, no SDK);
  - Claude Code hooks that never break a session;
  - a single-file viewer on `@relate/relate-wasm` and Cytoscape;
  - `heal`, on the Claude API with structured outputs.
- **Testing:** 20 tests, plus a benchmark that replays real Flask and Requests releases through the full pipeline.

## Challenges we ran into

- **Agents don't use memory just because it's there.** In our first unprompted run on Flask, Claude answered correctly and never touched memory. A once-per-session Stop hook ("you studied code and saved nothing; is anything durable worth keeping?") fixed it, and the agent now saves what it learned by itself.
- **Noise.** Our benchmark showed that 72% of Flask 2.3's "changes" were only rewritten type hints. Behaviour-level fingerprints cut the flagged memories on that release from 51% to 19%.
- **Scale.** Django's first scan crashed on Relate's 64 MiB per-commit safety cap, and re-syncing an unchanged repo took 12 s. Scans are now written in committed batches, and unchanged files are skipped by size, modification time and hash: 17 s for the first scan, about 1 s after that.
- **A Relate engine bug.** Deleting a node or relationship created earlier in the same open transaction panics. We isolated a minimal repro and work around it by committing before every delete phase.

## Accomplishments that we're proud of

- **An agent keeping its own memory honest** on a real, well-known codebase, without being told to.
- **Real numbers from real release history.** Suppose a memory cited every function across Flask 2.2 → 3.1 and Requests 2.28 → 2.32. Per release:
  - citing whole files would have flagged **58–100%** of those memories;
  - citing exact text would have flagged **5–98%**;
  - **Receipts flagged 4–19%**, while still catching every behaviour change.

  It followed 329 renames and moves, including Requests moving its whole package into `src/`.
- **Zero-install judging:** a one-line `uvx` demo, and a viewer that runs the database in your browser.

## What we learned

- **Embedded matters.** Because Relate is a library, the memory graph lives inside every hook and tool call instead of behind a service.
- **Precision matters more than coverage for trust.** A memory system that cries wolf gets ignored, so not flagging reformatting, renames or hint rewrites mattered as much as flagging real changes.
- **Tools aren't enough.** Agents need to be asked at the right moment: at session start, after an edit, and before they stop.

## What's next for Receipts: Agent Memory That Knows When It's Wrong

- **More languages,** with tree-sitter scanners beyond Python's AST and the current JS/TS scan.
- **Quote receipts that travel** with text moved across files.
- **`receipts heal` as a GitHub Action** that opens a PR updating memory after every merge.
- **Team-wide memory:** `relate push` to Neo4j Aura, so a team can query memory across all its repositories.
