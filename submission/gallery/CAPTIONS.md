# Gallery order and captions (upload in this order; the first image is the thumbnail)

1. `1-cover.png`: Receipts: every memory cites the code that proves it, and a Relate graph flags it when that code changes.
2. `2-stale-memory.png`: The viewer runs Relate in your browser. A stale memory shows why it was flagged and the exact diff that broke it.
3. `3-propagation.png`: One refactor, one sync. Relate walks the graph: stale, suspect via a callee's changed contract, suspect via derivation, and a followed rename.
4. `4-live-flask.png`: Real code, unprompted. Claude Code's Flask 2.3 memory went stale after the 3.1 upgrade, and the next session replaced it (M1 → M2).
5. `5-benchmark.png`: On real Flask and Requests releases, Receipts flags 4–19% of memories, against up to 100% for file-level receipts.
