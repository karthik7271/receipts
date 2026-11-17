# Demo video: 2 minutes

Record at 1080p with a large terminal font (about 18 pt). Before recording:
`uv run receipts demo --dir /tmp/rd --fast > /dev/null` (warm-up), then `rm -rf /tmp/rd`.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:12 | Title card or README hero | "Coding agents remember things about your code. Then the code changes and the memory doesn't. Receipts gives agent memory receipts, and Relate's graph decides what's still true." |
| 0:12–0:35 | `uv run receipts demo --dir /tmp/rd`, Day 1 part | "Every memory must cite code. A claim with no receipt is rejected. These eight memories cite symbols and exact config lines, and some build on others. Before touching shared code, the agent asks `impact`: three memories depend on `Money.times` through their callers." |
| 0:35–1:00 | The commit and the propagation output | "A teammate switches Money to Decimal, moves discounts into a new module and raises tax. On the next session start, one sync, and Relate walks the graph: M1 and M6 are stale because their code changed. M3's quoted config line is gone. M2's function was renamed and its receipt followed it, but `Money.times` changed signature two hops away, so it's suspect. M4 was only reformatted, but it builds on stale M1. Untouched memories stay fresh: no false alarms." |
| 1:00–1:15 | Day 10: `recall`, `verify M3` diff | "A fresh session asks about refunds. Instead of confidently saying integer cents, it gets a suspect memory, the reasons, and the exact diff." |
| 1:15–1:35 | Browser: GitHub Pages viewer, "before repair" dataset; click M2, then the Cypher tab and "Upstream drift" | "This is Relate compiled to WebAssembly, running in the tab. Every panel is a Cypher query; here's the two-hop drift path." |
| 1:35–1:52 | Viewer dataset "Live: Claude Code"; scroll `examples/live-claude-code/README.md` | "And this wasn't scripted. Claude Code saved these memories over MCP. After the refactor, the session-start hook told a new session that all seven were out of date, and it revised six and reaffirmed one." |
| 1:52–2:00 | README install block | "`receipts install`: an MCP server and hooks, one Relate file per repo. Memory with receipts." |

Optional, if you have an API key: show `receipts heal --dry-run` on `/tmp/rd` before the repair step (rerun the
demo up to Day 10 by stopping it with Ctrl-C after the propagation output).
