"""Serve a local browser game while keeping the Dify application key server-side."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from client import ROOT, run


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = (ROOT / "web" / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/action":
            self.send_error(404)
            return
        # Only the locally served page may invoke this local API.
        if self.headers.get("Origin") != "http://localhost:8090":
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1024:
                raise ValueError("Invalid input length")
            value = json.loads(self.rfile.read(size))
            body = json.dumps(run(value["command"], value["session_id"])["data"]["outputs"], ensure_ascii=False).encode()
            status = 200
        except Exception as error:
            body = json.dumps({"error": str(error)}, ensure_ascii=False).encode()
            status = 400
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print("打开 http://localhost:8090 开始游玩；Ctrl-C退出。", flush=True)
ThreadingHTTPServer(("127.0.0.1", 8090), Handler).serve_forever()
