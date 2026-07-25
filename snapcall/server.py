"""Local HTTP endpoint the band (or anything else) fires a snap into.

Deliberately stdlib-only so there is no dependency for the firmware side to
care about, and deliberately tiny — this is a seam, not an application.

    POST /trigger   {"source": "band"}   -> arm + place the call
    GET  /state                          -> current status (dashboard)
    GET  /                               -> one-page live view

The ESP32 does not need to know Callwright exists. It posts to /trigger and
this side owns everything after that.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .trigger import TriggerHub

log = logging.getLogger("snapcall.server")

PAGE = """<!doctype html><meta charset=utf-8><title>SnapCall</title>
<style>
 body{background:#0d0f13;color:#e8eaed;font:16px/1.6 -apple-system,system-ui,sans-serif;
      margin:0;display:grid;place-items:center;min-height:100vh}
 main{width:min(760px,92vw)}
 .state{font-size:3.2rem;font-weight:650;letter-spacing:-.02em;text-transform:lowercase}
 .idle{color:#5f6672}.armed{color:#f5c451}.calling{color:#61a8ff}
 .done{color:#4ade80}.cancelled{color:#9aa1ac}.error{color:#f87171}
 .detail{color:#9aa1ac;margin-top:.25rem}
 pre{background:#15181e;border:1px solid #232833;border-radius:12px;padding:1rem 1.25rem;
     white-space:pre-wrap;margin-top:1.75rem;max-height:46vh;overflow:auto;font-size:.95rem}
 .hint{color:#5f6672;font-size:.85rem;margin-top:1.25rem}
</style>
<main>
 <div class=state id=s>idle</div>
 <div class=detail id=d></div>
 <pre id=t></pre>
 <div class=hint>POST /trigger to fire</div>
</main>
<script>
async function tick(){
  try{
    const r = await fetch('/state'), j = await r.json();
    const s = document.getElementById('s');
    s.textContent = j.state; s.className = 'state ' + j.state;
    document.getElementById('d').textContent = j.detail || '';
    document.getElementById('t').textContent = (j.transcript||[]).join('\\n');
  }catch(e){}
  setTimeout(tick, 500);
}
tick();
</script>
"""


def make_handler(hub: TriggerHub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter than the default
            log.debug(fmt, *args)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return {}

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.startswith("/state"):
                status = hub.status
                self._json(
                    200,
                    {
                        "state": status.state,
                        "detail": status.detail,
                        "source": status.source,
                        "outcome": status.outcome,
                        "transcript": status.transcript,
                    },
                )
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            body = self._body()
            if self.path.startswith("/trigger"):
                action = hub.snap(source=body.get("source", "http"))
                self._json(202, {"action": action, "state": hub.status.state})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def serve(hub: TriggerHub, host: str = "0.0.0.0", port: int = 8787) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(hub))
    log.info("listening on http://%s:%d  (dashboard at /, trigger at POST /trigger)", host, port)
    server.serve_forever()
