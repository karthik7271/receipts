# Demo video: 2 minutes

Record at 1080p with a large terminal font (about 18 pt). Warm up first:
`uvx --from git+https://github.com/karthik7271/receipts receipts demo --dir /tmp/rd --fast > /dev/null`, then
`rm -rf /tmp/rd`.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:10 | README hero screenshot | "Coding agents remember things about your code. Then the code changes and the memory doesn't. Receipts makes every memory cite code, and a Relate graph decides what's still true." |
| 0:10–0:30 | `receipts demo`, Day 1 | "A claim with no receipt is rejected. These memories cite functions and exact config lines, and some build on others." |
| 0:30–0:55 | The refactor and the propagation output | "A teammate switches Money to Decimal, renames the discount function and moves it, and raises tax. One sync, and Relate walks the graph: these memories are stale because their code changed. This one followed the rename, but the function it calls changed its contract, so it's suspect. Untouched memories stay fresh." |
| 0:55–1:20 | `examples/live-flask/README.md`, scrolled slowly | "And on real code, unprompted: Claude Code learned how Flask picks error handlers and saved it on its own. We upgraded the repo to Flask 3.1, where that code moved. The next session was told the memory was stale before it read anything, checked the new code, and fixed its own memory." |
| 1:20–1:40 | Viewer on GitHub Pages: "before repair", click M1 (stamp and diff), then the Cypher tab's "signature change" sample | "This is Relate running in the browser. Every panel is a Cypher query. Here's the two-hop path that made a memory suspect." |
| 1:40–1:52 | README benchmark table | "On real Flask and Requests releases, citing files would flag up to 100% of memories. Receipts flags 4 to 19%, and followed 329 renames." |
| 1:52–2:00 | `receipts install` | "One command: an MCP server and hooks, one Relate file per repo. Memory with receipts." |
