# Where the server keeps what it learns while you play (marks, area names, calibration, last live reads).
# ANIIMO_DATA picks another folder, so a second copy of the server (e.g. a test copy) never writes yours.
import os

DATA = os.environ.get("ANIIMO_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
