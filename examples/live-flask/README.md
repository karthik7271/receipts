# Live example: Claude Code on Flask, unprompted

This is real Claude Code (model: Sonnet, headless `claude -p`) working in a checkout of
[Flask](https://github.com/pallets/flask) with Receipts installed (`receipts install`). **Neither prompt mentions
memory or Receipts.** The hooks and the MCP server's instructions are the only nudge.

1. **Flask 2.3.0.** The user asks how Flask picks an error handler. The agent answers. When it's about to stop, the
   Stop hook points out that it studied code and saved nothing, and it remembers one non-obvious fact, citing
   `_find_error_handler`.
2. **The repo moves to Flask 3.1.0.** Flask 3.0 split the app class into `flask/sansio/app.py`, and
   `_find_error_handler` gained a `blueprints` parameter.
3. **A fresh session asks an upgrade question.** Before the agent reads anything, the SessionStart hook tells it the
   memory is **stale**. It re-checks the new code, **revises** the memory to the new location (M1 → M2), and
   answers.

The store holds Flask's whole code graph plus the memories. Inspect it read-only (the Flask source isn't vendored
here, so don't run `sync` against this folder):

```bash
receipts --repo examples/live-flask list --status superseded
receipts --repo examples/live-flask why M2
```

or in the browser: pick "Live: Claude Code on Flask, unprompted" in the viewer.

---

## Session 1, Flask 2.3.0

What the SessionStart hook told the agent:
```
Receipts memory is empty for this repo. When you learn something durable about this codebase (an invariant, convention, gotcha, decision), save it with the `remember` tool and cite the code that proves it.
```

- `Grep` def (handle_user_exception|handle_exception|_find_error_handler|handle_http_exception|full_dispatch_request|wsgi_app)
- `Read` src/flask/app.py
- `remember` {"text": "Flask error handler precedence (App._find_error_handler): status-code-specific handlers beat exception-class handlers, even when the class handler is on a blueprint and the code handler is on the app. Within each tier, blueprint handlers (from request.blueprints) beat app handlers, then the exception's MRO is walked so the most specific class wins. Non-HTTP exceptions have code None, so only the class tier applies to them.", "cites": [{"path": "src/flask/app.py", "quote": "for c in (code, None) if code is not None else (None,):"}, {"symbol": "src/flask/app.py::Flask._find_error_handler"}], "kind": "fact", "tags": ["error-handling", "routing"]}

Answer to the user:

> I saved one memory to Receipts (M1). It records the handler lookup order in `_find_error_handler`. The order is code-specific handlers first, then blueprint before app, then the exception's MRO. The memory cites the loop at `src/flask/app.py:1234`, and Receipts checked that citation as fresh.

(6 turns, $0.07)

## Session 2, Flask 3.1.0

What the SessionStart hook told the agent:
```
Receipts: 1 memories about this repo (0 fresh). Call `recall` before relying on what you think you know about this codebase.
Since the last sync, 111 files changed.
These memories no longer match the code. Don't trust them as written; `verify` then `reaffirm`/`revise`/`retract` when you touch the related code:
- M1 [STALE] Flask error handler precedence (App._find_error_handler): status-code-specific handlers beat exception-class handlers, even when the class handler is on a blueprint and the code handler is on the app. Within each tier, blueprint handlers (from request.blueprints) beat app handlers, then the exception's MRO is walked so the most specific class wins. Non-HTTP exceptions have code None, so only the class tier applies to them.  (Flask._find_error_handler was deleted; quoted text is gone from src/flask/app.py)
```

- `Grep` def _find_error_handler|def handle_user_exception|def _get_exc_class_and_code
- `Read` src/flask/sansio/app.py
- `revise` {"id": "M1", "text": "Flask error handler precedence (sansio App._find_error_handler in src/flask/sansio/app.py): status-code-specific handlers beat exception-class handlers, even when the class handler is on a blueprint and the code handler is on the app. Within each tier, blueprint handlers (from request.blueprints) beat app handlers, then the exception's MRO is walked so the most specific class wins. Non-HTTP exceptions have code None, so only the class tier applies to them. Behavior unchanged in 3.1.0; the method moved from flask/app.py to the sansio base class.", "cites": [{"path": "src/flask/sansio/app.py", "symbol": "App._find_error_handler"}, {"path": "src/flask/sansio/app.py", "quote": "for c in (code, None) if code is not None else (None,):"}]}

Answer to the user:

> I revised the stale memory (M1 → M2) so it points at `src/flask/sansio/app.py`. Both of its citations check out against the current code. The error-handler precedence rules are the same as before.

(6 turns, $0.22)


---

The store contains code snippets from [Flask](https://github.com/pallets/flask), © Pallets, used under the BSD-3-Clause license.
