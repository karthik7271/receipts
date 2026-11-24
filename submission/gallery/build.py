"""Render the Devpost gallery images (1800x1200, 3:2) with headless Chrome.

    python submission/gallery/build.py

Pages in src/ are rendered as-is; the terminal card is generated from the real
`receipts demo` output captured in src/terminal-lines.json (FORCE_COLOR=1); the
viewer shots are taken from the live site.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SITE = "https://receipts-memory.vercel.app/"

ANSI = {"1": "font-weight:600", "2": "opacity:.62", "31": "color:#ff7b72", "32": "color:#5fcf8a",
        "33": "color:#e8b04a", "35": "color:#c79bf2", "36": "color:#6fc3c9", "90": "color:#7d8a85"}


def ansi_to_html(line: str) -> str:
    out, open_spans = [], 0
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = re.fullmatch(r"\x1b\[([0-9;]*)m", part)
        if not m:
            out.append(html.escape(part))
            continue
        codes = [c for c in m.group(1).split(";") if c]
        if not codes or codes == ["0"]:
            out.append("</span>" * open_spans)
            open_spans = 0
        else:
            style = ";".join(ANSI[c] for c in codes if c in ANSI)
            out.append(f'<span style="{style}">')
            open_spans += 1
    out.append("</span>" * open_spans)
    return "".join(out)


def terminal_page() -> Path:
    lines = json.loads((SRC / "terminal-lines.json").read_text())
    body = "\n".join(ansi_to_html(l) for l in lines)
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@500;600;700&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}}html,body{{margin:0;width:1800px;height:1200px;overflow:hidden}}
body{{background:#e5ebe2;font-family:"Instrument Sans",system-ui,sans-serif;color:#1e2a2b;-webkit-font-smoothing:antialiased;
  display:grid;grid-template-rows:auto 1fr;padding:60px 110px 60px}}
h1{{font-size:44px;letter-spacing:-.02em;margin:0 0 10px;font-weight:700}}
p{{font-size:23px;color:#5f6b66;margin:0 0 28px;max-width:72ch;line-height:1.45}}
.win{{min-height:0;background:#121917;border-radius:16px;box-shadow:0 30px 60px -30px rgba(18,25,23,.7);overflow:hidden;display:flex;flex-direction:column}}
.bar{{height:46px;display:flex;align-items:center;gap:10px;padding:0 22px;color:#7d8a85;font:500 18px "Spline Sans Mono",monospace;border-bottom:1px solid #26302d}}
pre{{margin:0;padding:22px 30px;color:#dfe7e2;font:400 18px/1.42 "Spline Sans Mono",monospace;white-space:pre}}
</style></head><body>
<header><h1>One refactor, one sync: Relate decides what is still true</h1>
<p>A teammate switches Money to Decimal, renames and moves a discount function, and raises tax. On the next session start, Receipts syncs the code graph and the memory statuses follow the graph.</p></header>
<div class="win"><div class="bar">receipts demo</div><pre>{body}</pre></div>
</body></html>"""
    out = SRC / "terminal.html"
    out.write_text(page)
    return out


def shoot(url: str, out: Path, *, width: int = 1800, height: int = 1200, scale: float = 1.0) -> None:
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={width},{height}", f"--force-device-scale-factor={scale}",
                        "--virtual-time-budget=20000", f"--user-data-dir={prof}", f"--screenshot={out}", url],
                       check=True, capture_output=True, timeout=180)
    print("wrote", out.relative_to(HERE.parent.parent), f"({out.stat().st_size // 1024} KB)")


def main() -> None:
    shoot((SRC / "cover.html").as_uri(), HERE / "1-cover.png")
    shoot(f"{SITE}?m=M1&theme=light", HERE / "2-stale-memory.png", width=1500, height=1000, scale=1.2)
    shoot(terminal_page().as_uri(), HERE / "3-propagation.png")
    shoot(f"{SITE}?data=live-flask.cypher&m=M1&theme=dark", HERE / "4-live-flask.png", width=1500, height=1000, scale=1.2)
    shoot((SRC / "benchmark.html").as_uri(), HERE / "5-benchmark.png")


if __name__ == "__main__":
    sys.exit(main())
