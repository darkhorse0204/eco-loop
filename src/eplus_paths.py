"""find the portable energyplus install and put its python api on the path.

the pyenergyplus package lives *inside* the energyplus folder, so we add that folder
to sys.path when this module is imported. we look in this order:

    1. $ECO_LOOP_EPLUS_DIR                 (explicit override)
    2. ./tools/EnergyPlus-*                (the portable zip that setup.py grabs)
    3. C:/EnergyPlusV*  /  /Applications/EnergyPlus-*   (normal system installs)
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _candidate_dirs() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("ECO_LOOP_EPLUS_DIR")
    if env:
        cands.append(Path(env))
    cands += [Path(p) for p in glob.glob(str(_REPO_ROOT / "tools" / "EnergyPlus*"))]
    cands += [Path(p) for p in glob.glob("C:/EnergyPlusV*")]
    cands += [Path(p) for p in glob.glob("C:/Program Files/EnergyPlus*")]
    cands += [Path(p) for p in glob.glob("/Applications/EnergyPlus*")]
    cands += [Path(p) for p in glob.glob("/usr/local/EnergyPlus*")]
    return cands


def find_energyplus_dir() -> Path:
    """give back the folder that holds the pyenergyplus package."""
    for d in _candidate_dirs():
        if d.is_dir() and (d / "pyenergyplus").is_dir():
            return d
        # the portable zip unpacks into a nested versioned folder
        for sub in d.glob("EnergyPlus*"):
            if (sub / "pyenergyplus").is_dir():
                return sub
    raise FileNotFoundError(
        "couldn't find energyplus. run `python scripts/setup.py` first, or point "
        "ECO_LOOP_EPLUS_DIR at your energyplus folder."
    )


def ensure_on_path() -> Path:
    """add energyplus to sys.path so `import pyenergyplus...` works. returns the dir."""
    d = find_energyplus_dir()
    ds = str(d)
    if ds not in sys.path:
        sys.path.insert(0, ds)
    # the dlls sit in the same folder, make sure windows can find them
    if hasattr(os, "add_dll_directory") and os.name == "nt":
        try:
            os.add_dll_directory(ds)
        except OSError:
            pass
    return d


if __name__ == "__main__":
    d = ensure_on_path()
    print("energyplus dir:", d)
    from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

    api = EnergyPlusAPI()
    print("energyplus api version:", api.api_version())
