# Demo video: script and recording guide

Target length: **1:50–2:00**. Speak at a relaxed pace; the narration below is about 270 words.

---

## Part 1. What this project is, in plain words (read this first)

**The problem.** AI coding agents (Claude Code, Cursor) now *remember* things about your codebase between sessions,
for example "money is stored as integer cents". Then someone changes the code, and the memory doesn't change. Next
session, the agent repeats the old fact with full confidence. A confidently wrong memory is worse than no memory.

**The idea.** Make every memory carry a **receipt**: proof in the code. A memory must point at the exact function or
line that makes it true. When that code changes, the memory is automatically flagged.

**The three words to use in the video:**
- **Stale:** the code this memory points at changed. Don't trust it.
- **Suspect:** something this memory depends on changed. Double-check it.
- **Fresh:** nothing relevant changed. It's still true.

**Where Relate comes in.** Relate is Neo4j's graph database that runs *inside* the program, with no server. Receipts
stores everything in one Relate graph: memories, the code (functions and who calls whom), every code change, and
which memories were built on which. "Is this memory still true?" becomes a question about *paths in a graph*: a
memory points at a function, that function calls one that changed, so the memory is suspect. That's the "connected
context" the hackathon is about.

**The proof it works.** On real code (Flask), Claude Code saved a memory without being asked to. After a Flask upgrade
moved that code, the next session was warned the memory was stale and fixed it itself.

**One-sentence pitch:** *Receipts is memory for coding agents where every memory cites the code that proves it, and a
Relate graph tells the agent the moment a memory stops being true.*

---

## Part 2. Before you record (10 minutes)

1. **Terminal:** make the font big (about 18 pt) and clear the screen. Run this once to warm the cache, so it starts
   instantly while recording:
   ```bash
   uvx --from git+https://github.com/karthik7271/receipts receipts demo --dir /tmp/rd --fast > /dev/null; rm -rf /tmp/rd
   ```
   Then type this, but don't press Enter yet:
   ```bash
   uvx --from git+https://github.com/karthik7271/receipts receipts demo --dir /tmp/rd
   ```
2. **Browser tabs, in this order:**
   1. https://receipts-memory.vercel.app/?m=M1 (the stale memory with its stamp and diff)
   2. https://github.com/karthik7271/receipts/tree/main/examples/live-flask (the real Flask sessions)
   3. https://receipts-memory.vercel.app/?data=live-flask.cypher&m=M2 (the Flask memory in the viewer)
   4. https://github.com/karthik7271/receipts#proven-on-real-code (the benchmark table)
3. **Recorder:** on a Mac, QuickTime (File → New Screen Recording) or OBS, at 1080p. Use a headset mic if you have
   one, and close notifications.

---

## Part 3. The script

| Time | On screen | Say |
|---|---|---|
| **0:00–0:15** | Tab 1: the viewer, memory M1 with the red STALE stamp. Hover over the diff. | "AI coding agents now remember things about your code between sessions. The problem: the code changes, and the memory doesn't. So next week your agent confidently tells you something that *used* to be true. This is Receipts: agent memory that knows when it's wrong." |
| **0:15–0:35** | Terminal: press Enter on `receipts demo`. Let Day 1 scroll: the rejected claim, then M1–M8. | "The rule is simple: no receipt, no memory. Every fact an agent saves has to cite the code that proves it, a function or an exact line of config. A claim with nothing behind it gets rejected. These eight memories each point at real code, and some build on others." |
| **0:35–1:00** | Terminal: the teammate's commit, then **Memory status changes**. Point at M1, M2 and M7. | "Now a teammate refactors: money becomes Decimal, a function is renamed and moved, tax goes up. Receipts keeps memories, code and every change in one Relate graph, and the graph works out what broke. Memories whose code changed are stale. This one followed the rename, but a function it relies on changed its contract, so it's suspect. And memories nobody touched stay fresh. No false alarms." |
| **1:00–1:22** | Tab 2: scroll the Flask README slowly. Then tab 3: the Flask memory in the viewer. | "That's a demo. Here's real code. I asked Claude Code a question about Flask without mentioning memory. It answered and saved what it learned. Then I upgraded the repo to Flask 3.1, where that code moved. The next session was warned the memory was stale before it read a single file, checked the new code, and fixed its own memory." |
| **1:22–1:42** | Tab 1: click **Cypher**, then the sample "Where did a signature change ripple two calls away?". Then back to Memory and click M2 in the list to show the highlighted path. | "Under the hood it's all Relate. Each repo gets one embedded file that every hook opens in milliseconds, and staleness is a few Cypher path queries. This viewer is Relate itself, running in your browser as WebAssembly." |
| **1:42–2:00** | Tab 4: the benchmark table. End on the README's install line. | "On real Flask and Requests releases, citing whole files would flag up to every memory. Receipts flags four to nineteen percent, and it followed 329 renames. One command to install. Receipts: memory with proof." |

---

## Part 4. Tips

- **Don't read the script word for word.** Know the beats: problem, rule, refactor, real code, Relate, numbers.
  Natural speech beats perfect wording.
- **If you're running long,** cut the Cypher scene (1:22–1:42) to one sentence. Never cut the Flask scene: it's
  the strongest proof.
- **Show, don't describe.** When you say "stale", the red stamp should be on screen. When you say "followed the
  rename", point at M2.
- **Upload** to YouTube (Public or Unlisted) with the title *Receipts: Agent Memory That Knows When It's Wrong (Relate
  by Neo4j Hackathon)*. Paste the link into Devpost's "Video demo link", and send it to me so I can add it to the
  README.

## Part 5. If a judge asks (Q&A cheat sheet)

- **"Why not just use a vector database?"** A vector store finds memories that *sound* related; it can't tell you a
  memory stopped being *true*. That needs the links between memories, code and changes: a graph.
- **"Doesn't every code change flag everything?"** No. Reformatting, comments, docstrings, type-hint rewrites and
  renames are ignored. On real Flask releases, Receipts flags 10–19% of memories, against 75–100% if memories cited
  whole files.
- **"Does the agent use it on its own?"** Yes. In the Flask run, neither prompt mentioned memory. Hooks tell the agent
  at the right moments: session start, after an edit, and before it stops.
- **"Why Relate?"** It runs inside the agent's tools with no server, so a hook can open the graph after every tool
  call in milliseconds. And one `.rl` file per repo can be committed and shared with the team.
- **"Does it scale?"** Django: 4,300 files and 40,000 functions. First scan 17 s; after that about 1 s per session
  and 0.1 s per edit.
