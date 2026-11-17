"""`receipts ui`: export the store and serve the in-browser viewer locally."""

from __future__ import annotations

import functools
import http.server
import shutil
import tempfile
import threading
import webbrowser
from importlib import resources
from pathlib import Path

from .export import to_cypher
from .store import Store


def build_site(store_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    viewer = resources.files("receipts") / "viewer" / "index.html"
    (out_dir / "index.html").write_bytes(viewer.read_bytes())
    with Store(store_path, read_only=True) as s:
        (out_dir / "graph.cypher").write_text(to_cypher(s))
    return out_dir


def serve(store_path: Path, *, port: int = 8765, open_browser: bool = True) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="receipts-ui-"))
    try:
        build_site(store_path, tmp)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp))
        handler.log_message = lambda *a, **k: None
        with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/"
            print(f"Receipts viewer at {url}  (Ctrl-C to stop)")
            if open_browser:
                threading.Timer(0.3, lambda: webbrowser.open(url)).start()
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
