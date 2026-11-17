# Submission checklist (deadline: Fri Dec 11 2026, 6:00 PM PT)

- [ ] Read the official rules at https://relate.devpost.com/rules (confirm requirements and the judging weights).
- [ ] Public GitHub repo `receipts`, with topic **`relate`** (`gh repo edit --add-topic relate`).
- [ ] GitHub Pages from `/docs` on `main` (Settings → Pages), then check https://karthik7271.github.io/receipts/ loads.
- [ ] Persistent Relate stores are committed: `examples/tinyshop/.receipts/memory.rl` and
      `examples/live-claude-code/tinyshop/.receipts/memory.rl`.
- [ ] From a clean machine or directory: `uvx --from git+https://github.com/karthik7271/receipts receipts demo`.
- [ ] Record the 1–2 minute video ([VIDEO_SCRIPT.md](VIDEO_SCRIPT.md)), upload it (YouTube or Vimeo), and put the link in README.md.
- [ ] Devpost: paste [DEVPOST.md](DEVPOST.md), add the repo, video and Pages links, and a screenshot of the viewer.
- [ ] AI disclosure: built with Claude Code; Claude Code is also the agent in the live example (already in README).
- [ ] Optional: file the relate-db transaction-delete panic upstream (minimal repro in README → "Found along the way").
- [ ] Optional: share in the Relate Discord / Neo4j Community for the community-favourite vote.
