# Where the game is installed, taken from the running game (the tools need it running anyway).
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from aniimo.memory import Process, find_pid  # noqa: E402

PACKAGE = os.path.join("cvs", "res", "uab", "win", "DefaultPackage")


def game_dir():
    """Install folder of the running Aniimo (the one holding Aniimo.exe)."""
    pid = find_pid()
    if not pid:
        sys.exit("Start the game first: the tools find its files through the running Aniimo.exe")
    proc = Process(pid)
    try:
        return os.path.dirname(proc.exe_path())
    finally:
        proc.close()


GAME = game_dir()
DATA = os.path.join(GAME, "Aniimo_Data")
BUNDLES = os.path.join(DATA, "StreamingAssets", PACKAGE)   # asset bundles (*.uab), patches included
MANIFESTS = os.path.join(DATA, PACKAGE)                     # asset manifests (*_stab.txt string tables)
