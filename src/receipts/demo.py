"""`receipts demo`: the whole story in about a minute, no API key needed.

It drives the same functions the MCP tools call, on a small sample repo
(tinyshop), across two "sessions" separated by a teammate's commit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from . import memory
from .export import to_cypher
from .graph import sync
from .render import Painter, badge, fact_block, impact_block, status_block, sync_summary, use_color, verify_block
from .store import Store

DAY1_FACTS = [
    dict(text="All money is stored as integer cents in Money; never use floats for amounts.",
         cites=["tinyshop/money.py::Money"], kind="convention"),
    dict(text="Discounts apply to the pre-tax subtotal and are capped at 50%.",
         cites=["apply_discount"], kind="fact"),
    dict(text="Sales tax is 8%, set in config/settings.yaml.",
         cites=[{"path": "config/settings.yaml", "quote": "tax_rate: 0.08"}], kind="fact"),
    dict(text="Refunds are prorated in integer cents and rounded to the nearest cent.",
         cites=["refund_amount"], kind="fact", derived_from=["M1"]),
    dict(text="Order total = discounted subtotal + tax on the discounted amount, so $100 with 10% off "
              "totals $97.20.", cites=["total"], kind="gotcha", derived_from=["M2", "M3"]),
    dict(text="Format money with str(Money), which renders $D.CC.", cites=["Money.__str__"], kind="howto"),
    dict(text="tinyshop is single-currency: CURRENCY is fixed to USD.",
         cites=["CURRENCY", {"note": "product decision from the team, Nov 2026"}], kind="decision"),
    dict(text="Shipping is free on orders over $50.",
         cites=[{"path": "config/settings.yaml", "quote": "free_shipping_over: 50"}], kind="fact"),
]


class Narrator:
    def __init__(self, fast: bool):
        self.p = Painter(use_color())
        self.fast = fast

    def pause(self, s: float = 0.6) -> None:
        if not self.fast:
            time.sleep(s)

    def title(self, text: str) -> None:
        bar = "━" * 72
        print("\n" + self.p(bar, "36"))
        print(self.p(f"  {text}", "36;1"))
        print(self.p(bar, "36"))
        self.pause(0.8)

    def step(self, text: str) -> None:
        print("\n" + self.p("▶ ", "35;1") + self.p.bold(text))
        self.pause(0.5)

    def say(self, text: str) -> None:
        print(self.p.dim(text))
        self.pause(0.3)

    def call(self, text: str) -> None:
        print(self.p("  $ ", "90") + self.p(text, "90"))
        self.pause(0.25)

    def out(self, text: str, indent: str = "  ") -> None:
        print("\n".join(indent + line for line in text.splitlines()))
        self.pause(0.35)


def _copy_tree(src, dst: Path) -> None:
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Receipts Demo",
                              "-c", "user.email=demo@example.invalid", "-c", "commit.gpgsign=false", *args],
                             capture_output=True, text=True, timeout=20)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def run(workdir: Path, *, fast: bool = False, keep: bool = False) -> Path:
    n = Narrator(fast)
    p = n.p
    demo_src = resources.files("receipts") / "demo_repo"
    if workdir.exists():
        if not keep and (workdir / ".receipts").exists():
            shutil.rmtree(workdir)
        elif any(workdir.iterdir()) and not (workdir / ".receipts").exists():
            sys.exit(f"{workdir} exists and is not a previous demo; pick another --dir")
    workdir.mkdir(parents=True, exist_ok=True)
    repo = workdir
    store_path = repo / ".receipts" / "memory.rl"
    _copy_tree(demo_src / "v1", repo)
    (repo / ".gitignore").write_text(".receipts/\n__pycache__/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tinyshop: money, pricing, checkout, refunds")

    n.title("Receipts: agent memory that knows when it's wrong")
    n.say("Coding agents remember things about your codebase. Then the code changes and the memory doesn't.")
    n.say("Receipts makes every memory cite code, keeps memories, code and their links in one Relate graph,")
    n.say("and lets graph traversal decide what is still true.")

    # ------------------------------------------------------------------ day 1
    n.step("Day 1. An agent explores tinyshop and remembers what it learns.")
    with Store(store_path) as s:
        rep = sync(s, repo)
        g = memory.status(s)["graph"]
    n.say(f"Relate now holds the code graph: {g['files']} files, {g['symbols']} symbols, {g['calls']} call edges.")

    n.call('remember("Shipping is free over $50", cites=["free_shipping"])')
    with Store(store_path) as s:
        try:
            memory.remember(s, repo, "Shipping is free over $50.", ["free_shipping"])
        except memory.MemoryError_ as e:
            n.out(p("✗ rejected: ", "31;1") + str(e))
    n.say("  No receipt, no memory. The agent cites the config line instead (that's M8 below).")

    with Store(store_path) as s:
        for f in DAY1_FACTS:
            fact = memory.remember(s, repo, f["text"], f["cites"], kind=f["kind"],
                                   derived_from=f.get("derived_from"))
            cites = ", ".join(c["label"] for c in fact["citations"])
            der = f"  builds on {', '.join(fact['derived_from'])}" if fact["derived_from"] else ""
            n.out(f"{p.bold(fact['id'])} {badge(fact['status'], p)}  {fact['text']}\n"
                  f"    {p.dim('receipts: ' + cites + der)}")

    n.call('impact("Money.times")   # the agent checks before touching shared code')
    with Store(store_path) as s:
        n.out(impact_block(memory.impact(s, repo, "Money.times"), p))

    # ------------------------------------------------------------------ the commit
    n.step("Day 9. A teammate ships a refactor. No one tells the agent.")
    _copy_tree(demo_src / "v2", repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Money -> Decimal; move discounts to promotions.py; tax 8.25%; tidy refunds")
    stat = _git(repo, "show", "--stat", "--format=%h %s", "HEAD")
    n.out(p.dim(stat.strip() or "(git not available; files updated in place)"))

    n.step("Next session starts (SessionStart hook runs `receipts sync`). Relate propagates the change:")
    with Store(store_path) as s:
        rep = sync(s, repo)
        before_repair = to_cypher(s)
    n.out(sync_summary(rep, p))
    n.say("\n  Read that again: Relate did four different things with one graph:")
    n.say("   • M1, M6: the cited code changed → stale.            M3: the quoted config line is gone → stale.")
    n.say("   • M2: apply_discount was renamed and moved; its receipt followed it. But discount_for calls")
    n.say("     Money.times, which now expects Decimal | str while discount_for still passes a float")
    n.say("     → suspect (upstream drift): exactly the kind of mismatch worth a second look.")
    n.say("   • M4: refunds.py was only reformatted (no change), but it builds on stale M1 → suspect.")
    n.say("   • M7, M8: untouched → still fresh. No false alarms.")

    # ------------------------------------------------------------------ day 10
    n.step('Day 10. A fresh session is asked: "How are partial refunds calculated?"')
    n.call('recall("how are partial refunds calculated")')
    with Store(store_path) as s:
        facts = memory.recall(s, repo, "how are partial refunds calculated", limit=4)
    for f in facts:
        n.out(fact_block(f, p))
    n.say("  Instead of confidently answering \"integer cents\", the agent knows to look again.")

    n.call('verify("M3")')
    with Store(store_path) as s:
        n.out(verify_block(memory.verify(s, repo, "M3"), p))

    n.step("The agent repairs its memory as part of the work:")
    with Store(store_path) as s:
        ops = [
            ("revise M3", lambda: memory.revise(s, repo, "M3", "Sales tax is 8.25%, set in config/settings.yaml.",
                                                cites=[{"path": "config/settings.yaml", "quote": "tax_rate: 0.0825"}])),
            ("revise M1", lambda: memory.revise(
                s, repo, "M1", "Money stores a Decimal dollar amount (Money.amount), quantized to the cent with "
                               "ROUND_HALF_UP; never use floats for amounts.")),
            ("reaffirm M2", lambda: memory.reaffirm(s, repo, "M2", note="discount_for still caps at 50% and "
                                                                       "applies before tax")),
            ("revise M4", lambda: memory.revise(s, repo, "M4", "Refunds are prorated with Decimal math and rounded "
                                                               "half-up to the cent.")),
            ("revise M5", lambda: memory.revise(
                s, repo, "M5", "Order total = discounted subtotal + tax on the discounted amount, so $100 with "
                               "10% off totals $97.43.")),
            ("revise M6", lambda: memory.revise(s, repo, "M6", "Format money with str(Money), which renders "
                                                               "$1,234.50 (thousands separators, two decimals).")),
        ]
        for label, op in ops:
            f = op()
            arrow = f"→ {p.bold(f['id'])}" if f["id"] != label.split()[1] else ""
            n.out(f"{p('✎', '35')} {label} {arrow} {badge(f['status'], p)}  {f['text']}")
        st = memory.status(s)
    n.out("\n" + status_block(st, p))

    site = repo / ".receipts" / "site"
    site.mkdir(parents=True, exist_ok=True)
    with Store(store_path, read_only=True) as s:
        (site / "after-repair.cypher").write_text(to_cypher(s))
    (site / "before-repair.cypher").write_text(before_repair)
    (site / "index.html").write_bytes((resources.files("receipts") / "viewer" / "index.html").read_bytes())
    (site / "datasets.json").write_text(json.dumps([
        {"file": "before-repair.cypher", "label": "Demo: after the refactor (before repair)"},
        {"file": "after-repair.cypher", "label": "Demo: after the agent repaired its memory"},
    ], indent=1))

    n.title("Done")
    rel = os.path.relpath(repo)
    where = rel if not rel.startswith("..") else str(repo)
    print(f"  Memory store (a Relate .rl file): {os.path.join(where, '.receipts', 'memory.rl')}")
    print(f"  Try:  receipts --repo {where} status")
    print(f"        receipts --repo {where} recall \"tax\"")
    print(f"        receipts --repo {where} why M12      # the graph behind one memory")
    print(f"        receipts --repo {where} ui           # explore the graph in your browser (Relate WASM)")
    return store_path
