# Live Aniimo map server. Reads the game read-only and serves the map page on the LAN.
# Usage: python server.py [--port 8777]
import argparse
import json
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from aniimo.game import GameReader  # noqa: E402
from calibration import Calibration  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
reader = GameReader()
calibration = Calibration()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB, **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        if self.path.startswith("/api/") or self.path in ("/", "/index.html", "/app.js", "/style.css", "/maps.json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            state = reader.snapshot()
            scene = state.get("scene")
            if scene:
                state["calibration"] = calibration.get(scene["mapId"])
            return self._json(state)
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        state = reader.snapshot()
        scene = (state.get("scene") or {}).get("mapId")
        if not scene:
            return self._json({"error": "The map you're on isn't known yet. " + state.get("message", "")}, 409)
        if path == "/api/calibration/add":
            pos = state.get("pos")
            if state.get("status") != "ok" or not pos:
                return self._json({"error": "Can't read your position right now: " + state.get("message", "")
                                   + ". Wait until the panel says Live, then click again."}, 409)
            calibration.add(scene, [pos["x"], pos["z"]], body["map"])
        elif path == "/api/calibration/undo":
            calibration.undo(scene)
        elif path == "/api/calibration/clear":
            calibration.clear(scene)
        else:
            return self._json({"error": "unknown endpoint"}, 404)
        return self._json(calibration.get(scene))


def lan_addresses():
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    reader.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    for ip in lan_addresses():
        print(f"Map: http://{ip}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()


if __name__ == "__main__":
    main()
