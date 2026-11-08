# Live Aniimo map server. Reads the game read-only and serves the map page on the LAN.
# Usage: python server.py [--port 8777] [--web]   (--web also opens the map in its own window)
import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from aniimo import areas, collection, handbook, marks, party, pickups, profile, quests, weather, wild  # noqa: E402
from aniimo.game import GameReader  # noqa: E402
from areas import AreaStore  # noqa: E402
from calibration import Calibration  # noqa: E402
from live import LiveStore  # noqa: E402
from marks import MarkStore, day_start  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Sites allowed to read this server's live data from the browser ("Connect to game" on the offline map).
# Only these: any other website you visit can't read your position or profile from this server.
# More can be added in ANIIMO_ALLOWED_ORIGINS (comma separated), e.g. a test copy of the site.
ALLOWED_ORIGINS = ("https://aniimo.thethiny.com",) + tuple(
    o.strip() for o in os.environ.get("ANIIMO_ALLOWED_ORIGINS", "").split(",") if o.strip())
COLLECT_SECONDS = 600   # names and marks appear in memory when the big map is opened; each pass scans for minutes
# Live reads run in their own lane (thread) so the minutes-long scans never hold them up; after a first
# search they take well under a second
LIVE_LANE = "live"
COPIES_LANE = "copies"   # the search for old player copies (eggs fallback) takes minutes: its own lane
COPIES_SECONDS = 600
HANDBOOK_SECONDS = 5    # seen / caught, tracked Aniimo, star title
WEATHER_SECONDS = 15    # weather and time of day
PROFILE_SECONDS = 5     # name, level, picture, Primegy, server resets
COLLECTION_SECONDS = 10  # the Aniimo you own (a few hundred small reads)
PARTY_LANE = "party"     # party members' positions every second: their own lane, so nothing waits on them
PARTY_SECONDS = 1
WILD_LANE = "nearby"     # what is loaded around you (wild Aniimo every second, pickups), on a lane of its own
WILD_SECONDS = 1
PICKUPS_SECONDS = 5      # unopened chests and pickups near you (they can come back after you've opened them)
area_store = AreaStore()
mark_store = MarkStore()
live = {"handbook": LiveStore("handbook", "states", keep=("blockCaught",)), "weather": LiveStore("weather", "weather"),
        "profile": LiveStore("profile", "name", keep=("eggs", "daily", "interacted")),   # known once their screen was opened
        "quests": LiveStore("quests", "tracked"), "collection": LiveStore("collection", "owned"),
        "party": LiveStore("party", "inGame", persist=False), "wild": LiveStore("wild", "inGame", persist=False),
        "pickups": LiveStore("pickups", "inGame", persist=False)}
reader = GameReader(collectors=[
    ("handbook", HANDBOOK_SECONDS, handbook.collect, live["handbook"].merge, LIVE_LANE),
    ("weather", WEATHER_SECONDS, weather.collect, live["weather"].merge, LIVE_LANE),
    ("profile", PROFILE_SECONDS, profile.collect, live["profile"].merge, LIVE_LANE),
    ("quests", PROFILE_SECONDS, quests.collect, live["quests"].merge, LIVE_LANE),
    ("collection", COLLECTION_SECONDS, collection.collect, live["collection"].merge, LIVE_LANE),
    ("party", PARTY_SECONDS, party.collect, live["party"].merge, PARTY_LANE),
    ("wild", WILD_SECONDS, wild.collect, live["wild"].merge, WILD_LANE),
    ("pickups", PICKUPS_SECONDS, pickups.collect, live["pickups"].merge, WILD_LANE),
    ("old copies", COPIES_SECONDS, handbook.find_copies, lambda scene, found: None, COPIES_LANE),
    ("area names", COLLECT_SECONDS, areas.collect, area_store.merge),
    ("marks", COLLECT_SECONDS, marks.collect, lambda scene, found: mark_store.merge(found)),
])
calibration = Calibration()


def today_start():
    """Your server's latest daily reset (epoch seconds): from the last one it ran (live), else from its region's
    day start in the config; None until your profile has been read."""
    profile = live["profile"].get()
    start = profile.get("dayReset") or (
        (mark_store.static.get().get("profile") or {}).get("serverDayStart", {}).get(str(profile.get("serverArea"))))
    return day_start(start, time.time()) if start else None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB, **kwargs)

    def log_message(self, *args):
        pass

    # Pages, code and data that change while you play or between builds; icons and tiles may be cached
    NO_STORE = ("/", "/index.html", "/app.js", "/style.css", "/maps.json", "/mapdata.json",
                "/collection.js", "/collection.css", "/collection.json", "/party.js", "/party.css", "/wild.js")

    def end_headers(self):
        path = urlparse(self.path).path   # "/?zoom=4" is still the page
        if path.startswith(("/api/", "/areas/")) or path in self.NO_STORE:
            self.send_header("Cache-Control", "no-store")
        if self.headers.get("Origin") in ALLOWED_ORIGINS:   # the offline map, connected to this server
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        """The browser's check before a public site calls this local server (CORS and Private Network
        Access preflight): yes for the allowed sites, nothing for any other."""
        self.send_response(204)
        if self.headers.get("Origin") in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

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
                if state.get("status") == "ok" and state.get("mapPos"):
                    pos = state["pos"]
                    calibration.observe(scene["mapId"], [pos["x"], pos["z"]], state["mapPos"])
                state["calibration"] = calibration.get(scene["mapId"])
            return self._json(state)
        if path == "/api/areas":
            scene = (reader.snapshot().get("scene") or {}).get("mapId")
            return self._json(area_store.get(scene) if scene else {})
        if path.startswith("/api/") and path[5:] in live:   # /api/handbook, /api/weather, /api/profile, /api/quests, /api/collection, /api/party, /api/wild
            return self._json(live[path[5:]].get())
        if path == "/api/marks":
            scene = (reader.snapshot().get("scene") or {}).get("mapId")
            return self._json(mark_store.get(scene, today_start(), live["profile"].get().get("interacted"),
                                              live["pickups"].get().get("standing")) if scene else {"marks": [], "types": {}, "groups": []})
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


def open_window(port):
    """Native window (Edge WebView2) on this PC; the LAN server keeps running alongside it."""
    import webview   # only needed for --web
    webview.create_window("Aniimo Live Map", f"http://127.0.0.1:{port}/", width=1280, height=820)
    webview.start()   # blocks until the window closes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--web", action="store_true", help="also open the map in its own window")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    reader.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    for ip in lan_addresses():
        print(f"Map: http://{ip}:{args.port}/")
    try:
        if args.web:
            threading.Thread(target=server.serve_forever, name="HTTP", daemon=True).start()
            open_window(args.port)
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if args.web:
            server.shutdown()
        reader.stop()


if __name__ == "__main__":
    main()
