# World (x, z) -> map pixel calibration per scene.
# Automatic: pairs of (world position, the minimap's own map-pixel position) are sampled while playing.
# Manual: points the user clicks, used only for maps that have no automatic fit yet.
# The game's maps are axis-aligned with one scale for both axes, so the fit is
#   px = s * x + bx,   py = flip * s * z + bz
import json
import math
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "calibration.json")
SAMPLE_SPACING = 25.0     # metres between kept automatic samples
MAX_SAMPLES = 24
OUTLIER_PX = 6.0          # a sample further than this from the fit is suspect
RESET_AFTER_OUTLIERS = 10 # this many suspect samples in a row: the old fit was wrong, start over
MIN_SCALE = 0.05          # map pixels per metre; anything smaller is a broken fit


def fit_uniform(points):
    """{x: [a, b], z: [a, b], error} with |a| shared, or None until two distinct points exist."""
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(p["world"][0] for p in points) / n
    mz = sum(p["world"][1] for p in points) / n
    mpx = sum(p["map"][0] for p in points) / n
    mpy = sum(p["map"][1] for p in points) / n
    den = sum((p["world"][0] - mx) ** 2 + (p["world"][1] - mz) ** 2 for p in points)
    if den < 1.0:
        return None
    best = None
    for flip in (-1.0, 1.0):   # -1: north is up (z grows upwards on the image)
        s = sum((p["world"][0] - mx) * (p["map"][0] - mpx) + flip * (p["world"][1] - mz) * (p["map"][1] - mpy)
                for p in points) / den
        err = sum((s * (p["world"][0] - mx) - (p["map"][0] - mpx)) ** 2
                  + (flip * s * (p["world"][1] - mz) - (p["map"][1] - mpy)) ** 2 for p in points)
        if best is None or err < best[0]:
            best = (err, s, flip)
    err, s, flip = best
    if abs(s) < MIN_SCALE:
        return None   # degenerate: the map positions did not move with the world positions
    return {"x": [s, mpx - s * mx], "z": [flip * s, mpy - flip * s * mz], "error": math.sqrt(err / n)}


def apply(fit, world):
    return [fit["x"][0] * world[0] + fit["x"][1], fit["z"][0] * world[1] + fit["z"][1]]


class Calibration:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        if data and "manual" not in data and "auto" not in data:
            data = {"manual": data}   # older files held only manual points
        self.manual = data.get("manual", {})
        self.auto = data.get("auto", {})    # scene -> {samples: [...]}
        self._fits = {scene: fit_uniform(entry["samples"]) for scene, entry in self.auto.items()}
        self._outliers = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump({"manual": self.manual, "auto": self.auto}, open(self.path, "w", encoding="utf-8"), indent=1)

    # ---- automatic ----

    def observe(self, scene, world, pixel):
        """Feed a (world, minimap pixel) pair; keeps a spread-out set of samples per scene."""
        scene = str(scene)
        with self.lock:
            samples = self.auto.setdefault(scene, {"samples": []})["samples"]
            fit = self._fits.get(scene)
            if fit:
                px = apply(fit, world)
                if math.dist(px, pixel) > OUTLIER_PX:
                    self._outliers[scene] = self._outliers.get(scene, 0) + 1
                    if self._outliers[scene] >= RESET_AFTER_OUTLIERS:
                        samples.clear()
                        self._fits[scene] = None
                        self._outliers[scene] = 0
                    return
                self._outliers[scene] = 0
            if any(math.dist(world, s["world"]) < SAMPLE_SPACING for s in samples):
                return
            samples.append({"world": world, "map": pixel})
            if len(samples) > MAX_SAMPLES:
                samples.pop(1)   # keep the first (anchor) and the newest spread
            self._fits[scene] = fit_uniform(samples)
            self._save()

    # ---- manual ----

    def add(self, scene, world, pixel):
        with self.lock:
            self.manual.setdefault(str(scene), []).append({"world": world, "map": pixel})
            self._save()

    def undo(self, scene):
        with self.lock:
            if self.manual.get(str(scene)):
                self.manual[str(scene)].pop()
                self._save()

    def clear(self, scene):
        with self.lock:
            self.manual.pop(str(scene), None)
            self._save()

    def get(self, scene):
        """{source: auto|manual|None, points, x, z, error} for a scene."""
        scene = str(scene)
        with self.lock:
            points = list(self.manual.get(scene, []))
            fit, source = self._fits.get(scene), "auto"
            if not fit:
                fit, source = fit_uniform(points), "manual"
        fit = fit or {}
        return {"source": source if fit else None, "points": points,
                "x": fit.get("x"), "z": fit.get("z"), "error": fit.get("error")}
