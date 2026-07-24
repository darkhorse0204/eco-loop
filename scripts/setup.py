"""one-time environment setup, safe to re-run.

  1. download + extract portable energyplus 25.1 into tools/ (no admin needed).
  2. make sure ollama is installed and pull the llm model.

usage:  python scripts/setup.py
"""
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
TOOLS.mkdir(exist_ok=True)

EPLUS_ZIP_URL = (
    "https://github.com/NatLabRockies/EnergyPlus/releases/download/"
    "v25.1.0/EnergyPlus-25.1.0-68a4a7c774-Windows-x86_64.zip"
)
MODEL = "llama3.1:8b"


def have_energyplus() -> bool:
    return any((TOOLS).glob("EnergyPlus*/pyenergyplus"))


def setup_energyplus():
    if have_energyplus():
        print("[setup] energyplus already here -> skip")
        return
    zip_path = TOOLS / "eplus.zip"
    if not zip_path.exists():
        print(f"[setup] downloading energyplus (~173 mb) ...")
        urllib.request.urlretrieve(EPLUS_ZIP_URL, zip_path)
    print("[setup] extracting energyplus ...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(TOOLS)
    print("[setup] energyplus ready:", next(TOOLS.glob("EnergyPlus*")))


def ollama_bin() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    win = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    return str(win) if win.exists() else None


def setup_llm():
    exe = ollama_bin()
    if not exe:
        print("[setup] ollama not found. grab it from https://ollama.com/download "
              "(per-user, no admin), then run this again.")
        return
    print(f"[setup] ollama found: {exe}")
    try:
        subprocess.run([exe, "pull", MODEL], check=True)
        print(f"[setup] model {MODEL} ready")
    except subprocess.CalledProcessError as e:
        print(f"[setup] `ollama pull {MODEL}` failed ({e}). is the ollama service running?")


if __name__ == "__main__":
    setup_energyplus()
    setup_llm()
    print("\n[setup] done. next: python scripts/build_model.py && python -m src.orchestrator")
