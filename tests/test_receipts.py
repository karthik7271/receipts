import io
import json
from importlib import resources
from pathlib import Path

import pytest

from receipts import memory
from receipts.codegraph import scan_python
from receipts.export import to_cypher
from receipts.graph import find_quote, sync
from receipts.hooks import post_tool_use, session_start
from receipts.mcp_server import Server
from receipts.store import Store

DEMO = resources.files("receipts") / "demo_repo"


def copy_tree(src, dst: Path) -> None:
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            copy_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPTS_REPO", str(tmp_path / "repo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    copy_tree(DEMO / "v1", repo)
    store = tmp_path / "memory.rl"
    with Store(store) as s:
        sync(s, repo)
    return repo, store


def remember_day1(repo, store):
    from receipts.demo import DAY1_FACTS
    with Store(store) as s:
        for f in DAY1_FACTS:
            memory.remember(s, repo, f["text"], f["cites"], kind=f["kind"], derived_from=f.get("derived_from"))


def apply_v2(repo):
    copy_tree(DEMO / "v2", repo)


def statuses(store):
    with Store(store) as s:
        return {r["id"]: r["status"] for r in s.q("MATCH (f:Fact) RETURN f.id AS id, f.status AS status")}


# --------------------------------------------------------------------------- code graph


def test_semantic_hash_ignores_formatting_comments_and_docstrings():
    a = scan_python("m.py", "def f(x):\n    '''doc'''\n    return x + 1\n")
    b = scan_python("m.py", "def f(x):\n    # a comment\n    return (\n        x + 1\n    )\n")
    assert a.symbols[0].body_hash == b.symbols[0].body_hash


def test_rename_keeps_body_hash_and_signature_change_is_detected():
    a = scan_python("m.py", "def old_name(x: int) -> int:\n    y = x * 2\n    return y + 1\n")
    b = scan_python("n.py", "def new_name(x: int) -> int:\n    y = x * 2\n    return y + 1\n")
    c = scan_python("m.py", "def old_name(x: float) -> int:\n    y = x * 2\n    return y + 1\n")
    d = scan_python("m.py", "def old_name(value: int) -> int:\n    y = value * 2\n    return y + 1\n")
    assert a.symbols[0].body_hash == b.symbols[0].body_hash
    assert a.symbols[0].types_hash != c.symbols[0].types_hash   # type change: tracked separately
    assert a.symbols[0].sig_hash != d.symbols[0].sig_hash       # parameter renamed: signature change


def test_equivalent_type_hints_hash_the_same():
    old = "import typing as t\ndef f(x: t.Optional[str], y: t.Dict[str, int]) -> 't.Union[int, str]':\n    return 1\n"
    new = "def f(x: str | None, y: dict[str, int]) -> str | int:\n    '''now documented'''\n    return 1\n"
    real = "def f(x: int | None, y: dict[str, int]) -> str | int:\n    return 1\n"
    h = lambda src: [s for s in scan_python("m.py", src).symbols if s.name == "f"][0]
    assert (h(old).body_hash, h(old).sig_hash, h(old).types_hash) == (h(new).body_hash, h(new).sig_hash, h(new).types_hash)
    # a real type change is tracked (it can make dependants suspect) but doesn't touch behaviour or shape
    assert h(real).types_hash != h(new).types_hash
    assert (h(real).body_hash, h(real).sig_hash) == (h(new).body_hash, h(new).sig_hash)
    added = "def f(x: str | None, y: dict[str, int], z: int = 0) -> str | int:\n    return 1\n"
    assert h(added).sig_hash != h(new).sig_hash  # a new parameter is a real signature change


def test_old_stores_are_migrated_without_false_alarms(shop):
    repo, store = shop
    remember_day1(repo, store)
    with Store(store) as s:  # pretend the store was built by an older hashing scheme
        s.q("MATCH (s:Symbol) SET s.body_hash = 'old-' + s.body_hash, s.sig_hash = 'old-' + s.sig_hash")
        s.q("MATCH (:Fact)-[c:CITES {mode: 'symbol'}]->() SET c.hash = 'old-' + c.hash, c.sig_hash = 'old-' + c.sig_hash")
        s.q("MATCH (m:Meta {key: 'receipts'}) SET m.hash_version = 1")
    with Store(store) as s:
        sync(s, repo)
    assert set(statuses(store).values()) == {"fresh"}
    apply_v2(repo)
    with Store(store) as s:
        sync(s, repo)
    assert statuses(store)["M1"] == "stale" and statuses(store)["M7"] == "fresh"


def test_quote_matching_respects_token_boundaries():
    assert find_quote("tax_rate: 0.0825\n", "tax_rate: 0.08") is None
    assert find_quote("a: 1\ntax_rate: 0.08\n", "tax_rate: 0.08") == 2
    assert find_quote("x   =   1", "x = 1") == 1


# --------------------------------------------------------------------------- scale & portability


def test_tiny_batches_give_identical_results(shop, monkeypatch):
    """Large repos are written in committed batches; batching must not change any outcome."""
    import receipts.graph as graph
    monkeypatch.setattr(graph, "BATCH", 3)
    repo, store = shop
    remember_day1(repo, store)
    apply_v2(repo)
    with Store(store) as s:
        sync(s, repo)
    assert statuses(store) == {"M1": "stale", "M2": "suspect", "M3": "stale", "M4": "suspect", "M5": "stale",
                               "M6": "stale", "M7": "fresh", "M8": "fresh"}


def test_touching_a_file_is_not_a_change(shop):
    import os
    repo, store = shop
    os.utime(repo / "tinyshop" / "money.py", None)  # new mtime, same content
    with Store(store) as s:
        report = sync(s, repo)
    assert report.sync_id is None and report.files_changed == []


def test_windows_lock_fallback(tmp_path, monkeypatch):
    import receipts.store as st

    class FakeMsvcrt:
        LK_NBLCK, LK_UNLCK = 2, 0
        calls = []

        @classmethod
        def locking(cls, fd, mode, n):
            cls.calls.append(mode)

    monkeypatch.setattr(st, "fcntl", None)
    monkeypatch.setattr(st, "msvcrt", FakeMsvcrt)
    with Store(tmp_path / "w.rl") as s:
        s.q("CREATE (:X)")
    assert FakeMsvcrt.calls == [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK]


# --------------------------------------------------------------------------- memory


def test_memories_need_real_receipts(shop):
    repo, store = shop
    with Store(store) as s:
        with pytest.raises(memory.MemoryError_, match="receipt"):
            memory.remember(s, repo, "uncited claim", [])
        with pytest.raises(memory.MemoryError_, match="no symbol"):
            memory.remember(s, repo, "claim", ["does_not_exist"])
        with pytest.raises(memory.MemoryError_, match="quote not found"):
            memory.remember(s, repo, "claim", [{"path": "config/settings.yaml", "quote": "tax_rate: 0.5"}])


def test_refactor_propagates_through_the_graph(shop):
    repo, store = shop
    remember_day1(repo, store)
    assert set(statuses(store).values()) == {"fresh"}

    apply_v2(repo)
    with Store(store) as s:
        report = sync(s, repo)
    assert ("tinyshop/pricing.py::apply_discount", "tinyshop/promotions.py::discount_for") in report.moved
    st = statuses(store)
    assert st == {"M1": "stale", "M2": "suspect", "M3": "stale", "M4": "suspect", "M5": "stale",
                  "M6": "stale", "M7": "fresh", "M8": "fresh"}

    with Store(store) as s:
        m2 = memory.get_fact(s, "M2")
        m4 = memory.get_fact(s, "M4")
    # the receipt followed the rename; the suspicion comes from two hops away
    assert m2["citations"][0]["target"] == "tinyshop/promotions.py::discount_for"
    assert any("Money.times" in r for r in m2["reasons"])
    # refunds.py was only reformatted: its own receipt is intact
    assert not m4["citations"][0]["broken"]
    assert any("builds on M1" in r for r in m4["reasons"])


def test_repairing_memory_restores_freshness_and_keeps_history(shop):
    repo, store = shop
    remember_day1(repo, store)
    apply_v2(repo)
    with Store(store) as s:
        sync(s, repo)
        with pytest.raises(memory.MemoryError_):
            memory.reaffirm(s, repo, "M3")  # the quote is gone; must revise
        m9 = memory.revise(s, repo, "M3", "Sales tax is 8.25%.",
                           cites=[{"path": "config/settings.yaml", "quote": "tax_rate: 0.0825"}])
        m10 = memory.revise(s, repo, "M1", "Money stores a Decimal amount quantized to cents.")
        memory.reaffirm(s, repo, "M2")
        m11 = memory.revise(s, repo, "M4", "Refunds use Decimal math, rounded half-up to the cent.")
        assert m11["derived_from"] == [m10["id"]]
        m12 = memory.revise(s, repo, "M5", "$100 with 10% off totals $97.43.")
        assert sorted(m12["derived_from"]) == sorted(["M2", m9["id"]])
        memory.revise(s, repo, "M6", "str(Money) renders $1,234.50.")
    st = statuses(store)
    assert {k: v for k, v in st.items() if v != "superseded"} == {
        "M2": "fresh", "M7": "fresh", "M8": "fresh", "M9": "fresh", "M10": "fresh", "M11": "fresh",
        "M12": "fresh", "M13": "fresh"}
    assert sum(1 for v in st.values() if v == "superseded") == 5


def test_recall_uses_connected_code(shop):
    repo, store = shop
    remember_day1(repo, store)
    with Store(store) as s:
        hits = {f["id"]: f["why"] for f in memory.recall(s, repo, target="tinyshop/checkout.py::total")}
    assert hits["M5"].startswith("cites")
    assert "calls apply_discount" in hits["M2"]  # memory about a callee, reached via CALLS


def test_impact_reaches_callers_and_derivations(shop):
    repo, store = shop
    remember_day1(repo, store)
    with Store(store) as s:
        imp = memory.impact(s, repo, "Money.times")
    assert {r["id"] for r in imp["via_callers"]} == {"M2", "M4", "M5"}


# --------------------------------------------------------------------------- agent surfaces


def test_mcp_server_speaks_jsonrpc(shop):
    repo, store = shop
    remember_day1(repo, store)
    srv = Server(repo, store)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "recall", "arguments": {"query": "tax"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "remember", "arguments": {"text": "x", "cites": ["nope"]}}},
    ]
    out = io.StringIO()
    srv.serve(io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert {t["name"] for t in replies[1]["result"]["tools"]} >= {"recall", "remember", "verify", "impact"}
    assert "M3" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["result"]["isError"] is True


def test_hooks_tell_the_agent_what_it_just_broke(shop):
    repo, store = shop
    remember_day1(repo, store)
    (repo / "config" / "settings.yaml").write_text("tax_rate: 0.09\ncurrency: USD\nfree_shipping_over: 50\n")
    msg = post_tool_use({"tool_name": "Edit", "tool_input": {"file_path": str(repo / "config/settings.yaml")}},
                        repo, store)
    assert "M3" in msg and "STALE" in msg
    start = session_start({}, repo, store)
    assert "M3" in start and "recall" in start


def test_recall_accepts_improvised_argument_names(shop):
    repo, store = shop
    remember_day1(repo, store)
    srv = Server(repo, store)
    assert "M3" in srv.call("recall", {"question": "tax"})
    assert "Pass `query`" in srv.call("recall", {})


class FakeClaude:
    """Stands in for anthropic.Anthropic(): returns canned verdicts per memory."""

    def __init__(self, verdicts):
        self.verdicts, self.seen = verdicts, []
        self.beta = self
        self.messages = self

    def create(self, **kw):
        case = json.loads(kw["messages"][0]["content"].split("\n\n", 1)[1])
        self.seen.append(case)
        assert kw["output_config"]["format"]["type"] == "json_schema"
        v = self.verdicts[case["memory_id"]]

        class Block:
            type, text = "text", json.dumps({"reason": "test", "revised_text": "", "new_receipts": [], **v})

        class Resp:
            stop_reason, content = "end_turn", [Block()]
        return Resp()


def test_heal_applies_verdicts_parents_first(shop):
    from receipts.heal import heal
    repo, store = shop
    remember_day1(repo, store)
    apply_v2(repo)
    fake = FakeClaude({
        "M1": {"verdict": "revise", "revised_text": "Money stores a Decimal amount quantized to cents."},
        "M2": {"verdict": "reaffirm"},
        "M3": {"verdict": "revise", "revised_text": "Sales tax is 8.25%.",
               "new_receipts": [{"kind": "quote", "target": "config/settings.yaml", "quote": "tax_rate: 0.0825"}]},
        "M4": {"verdict": "reaffirm"},
        "M5": {"verdict": "revise", "revised_text": "$100 with 10% off totals $97.43."},
        "M6": {"verdict": "retract"},
    })
    results = heal(repo, store, client=fake)
    order = [c["memory_id"] for c in fake.seen]
    assert order.index("M1") < order.index("M4") and order.index("M3") < order.index("M5")
    m3_case = next(c for c in fake.seen if c["memory_id"] == "M3")
    assert m3_case["receipts"][0]["quote_still_present"] is False
    st = statuses(store)
    assert st["M6"] == "retracted"
    assert all(v in ("fresh", "superseded", "retracted") for v in st.values()), st
    assert {r["outcome"] for r in results} >= {"revised", "reaffirmed", "retracted"}


def test_export_replays_into_relate(shop):
    import relate
    repo, store = shop
    remember_day1(repo, store)
    with Store(store, read_only=True) as s:
        script = to_cypher(s)
    db = relate.connect(":memory:")
    for stmt in script.split(";\n"):
        stmt = "\n".join(l for l in stmt.splitlines() if not l.startswith("//")).strip()
        if stmt:
            db.execute(stmt)
    assert list(db.execute("MATCH (f:Fact) RETURN count(f) AS c"))[0]["c"] == 8
    # 8 memories, one receipt each except M7 (a symbol plus a note)
    assert list(db.execute("MATCH (:Fact)-[r:CITES]->() RETURN count(r) AS c"))[0]["c"] == 9
