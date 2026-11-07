# Live game state that the page shows as is (your handbook, the weather), as last read from the game.
# Each is kept in data/<name>.json so the page still has it while the game is closed.
import json
import os
import threading
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class LiveStore:
    def __init__(self, name, required, keep=()):
        """`required`: result key that must be non-empty for a read to count (empty means not loaded yet).
        `keep`: keys whose None means "not known right now": the last known value stays."""
        self.path = os.path.join(DATA, f"{name}.json")
        self.required, self.keep = required, keep
        self.lock = threading.Lock()
        self.data = json.load(open(self.path, encoding="utf-8")) if os.path.exists(self.path) else {}

    def merge(self, scene, found):
        if not found.get(self.required):
            return   # not loaded in game yet (login, loading screen): keep the last good read
        with self.lock:
            found = dict(found, **{k: self.data[k] for k in self.keep if found.get(k) is None and k in self.data})
            as_json = lambda d: json.loads(json.dumps(d))   # same key types whether read from file or memory
            changed = as_json({k: v for k, v in self.data.items() if k != "updated"}) != as_json(found)
            self.data = dict(found, updated=int(time.time()))
            if changed:   # reads come every few seconds; only rewrite the file when something changed
                os.makedirs(DATA, exist_ok=True)
                json.dump(self.data, open(self.path, "w", encoding="utf-8"))

    def get(self):
        with self.lock:
            return dict(self.data)
