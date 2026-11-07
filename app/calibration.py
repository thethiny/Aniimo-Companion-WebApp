# World (x, z) -> map pixel calibration per scene, fitted from reference points.
# The game's maps are axis-aligned with one scale for both axes, so the fit is
#   px = s * x + bx,   py = flip * s * z + bz
# (3 unknowns + the flip). Two points over-determine it, so click errors average out and the
# north-south scale no longer depends on the points being far apart in Z.
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "calibration.json")


def fit_uniform(points):
    """{x: [a, b], z: [a, b]} with |a| shared, or None until two distinct points exist."""
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(p["world"][0] for p in points) / n
    mz = sum(p["world"][1] for p in points) / n
    mpx = sum(p["map"][0] for p in points) / n
    mpy = sum(p["map"][1] for p in points) / n
    den = sum((p["world"][0] - mx) ** 2 + (p["world"][1] - mz) ** 2 for p in points)
    if den < 1e-6:
        return None
    best = None
    for flip in (-1.0, 1.0):   # -1: north is up (z grows upwards on the image)
        s = sum((p["world"][0] - mx) * (p["map"][0] - mpx) + flip * (p["world"][1] - mz) * (p["map"][1] - mpy)
                for p in points) / den
        err = sum((s * (p["world"][0] - mx) - (p["map"][0] - mpx)) ** 2
                  + (flip * s * (p["world"][1] - mz) - (p["map"][1] - mpy)) ** 2 for p in points)
        if best is None or err < best[0]:
            best = (err, s, flip)
    _, s, flip = best
    return {"x": [s, mpx - s * mx], "z": [flip * s, mpy - flip * s * mz], "error": (best[0] / n) ** 0.5}


class Calibration:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        self.points = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump(self.points, open(self.path, "w", encoding="utf-8"), indent=1)

    def add(self, scene, world, pixel):
        with self.lock:
            self.points.setdefault(str(scene), []).append({"world": world, "map": pixel})
            self._save()

    def undo(self, scene):
        with self.lock:
            if self.points.get(str(scene)):
                self.points[str(scene)].pop()
                self._save()

    def clear(self, scene):
        with self.lock:
            self.points.pop(str(scene), None)
            self._save()

    def get(self, scene):
        """{points, x: [a, b] | None, z: [a, b] | None, error: rms px | None} for a scene."""
        with self.lock:
            pts = list(self.points.get(str(scene), []))
        fit = fit_uniform(pts) or {}
        return {"points": pts, "x": fit.get("x"), "z": fit.get("z"), "error": fit.get("error")}
