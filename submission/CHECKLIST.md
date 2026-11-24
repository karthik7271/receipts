# Submission checklist (deadline: Fri Dec 11 2026, 6:00 PM PT)

- [x] Public GitHub repo with topic `relate`: https://github.com/karthik7271/receipts
- [x] Persistent Relate stores committed in `examples/` (tinyshop demo, live Flask, live tinyshop)
- [x] Mirror on GitHub Pages: https://karthik7271.github.io/receipts/
- [x] One-line demo from GitHub verified: `uvx --from git+https://github.com/karthik7271/receipts receipts demo`
- [x] Live on Vercel: https://receipts-memory.vercel.app/ (run `scripts/deploy_vercel.sh` after any change to `docs/`)
- [ ] Read the official rules at https://relate.devpost.com/rules (confirm the requirements and judging weights)
- [ ] Record the 1–2 minute video ([VIDEO_SCRIPT.md](VIDEO_SCRIPT.md)), upload it, and add the link to README.md
- [ ] Devpost: paste [DEVPOST.md](DEVPOST.md), and add the repo, live link and video, plus 3–4 screenshots
      (`assets/viewer.png`, the demo's propagation output, the Flask example, the benchmark table)
- [ ] Submit early (by Dec 9), then share in the Relate Discord and Neo4j Community for the community vote
- [ ] Optional: report the relate-db transaction-delete panic upstream with the minimal repro
