"""self-correction: run a model, read the energyplus errors out of the .err log,
and fix the input file on its own - no human editing the code.

the failure i demo here is an out-of-range Timestep. energyplus fatals with a clear
severe message; the agent reads it, asks the llm for the corrected value (with a
deterministic fallback if the llm is down), rewrites the idf, and re-runs clean.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import eplus_paths

eplus_paths.ensure_on_path()
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402


def run_energyplus(idf: str, epw: str, out_dir: str) -> int:
    """run one sim, hand back the energyplus return code (0 = ok)."""
    api = EnergyPlusAPI()
    st = api.state_manager.new_state()
    api.runtime.set_console_output_status(st, False)
    rc = api.runtime.run_energyplus(st, ["-w", epw, "-d", out_dir, idf])
    return rc


def parse_errors(err_path: Path) -> dict:
    """boil a big .err log down to a tiny digest - we never hand the raw log to the llm."""
    if not err_path.exists():
        return {"fatal": [], "severe": [], "n_warnings": 0}
    lines = err_path.read_text(errors="replace").splitlines()
    severe, fatal, warn = [], [], 0
    for i, ln in enumerate(lines):
        if "** Severe" in ln:
            # grab the continuation line too, it usually has the actual detail
            detail = ln.strip()
            if i + 1 < len(lines) and "**  ~~~" in lines[i + 1]:
                detail += " " + lines[i + 1].strip()
            severe.append(detail)
        elif "** Fatal" in ln:
            fatal.append(ln.strip())
        elif "** Warning" in ln:
            warn += 1
    return {"fatal": fatal, "severe": severe[:8], "n_warnings": warn}


def llm_diagnose_timestep(digest: dict, base_url: str, model: str) -> int | None:
    """let the llm read the digest and hand back a valid timestep. none if it can't."""
    tool = {
        "type": "function",
        "function": {
            "name": "fix_timestep",
            "description": "Provide a corrected EnergyPlus Timestep value (timesteps per hour, must divide 60 evenly).",
            "parameters": {
                "type": "object",
                "properties": {
                    "timesteps_per_hour": {"type": "integer",
                                           "description": "A divisor of 60, e.g. 4 or 6."},
                    "reason": {"type": "string"},
                },
                "required": ["timesteps_per_hour", "reason"],
            },
        },
    }
    msg = (
        "An EnergyPlus run failed. Here is the parsed error digest:\n"
        + json.dumps(digest)
        + "\nThe Timestep object value is invalid. Call fix_timestep with a corrected "
          "value that divides 60 (choose 6 for a 10-minute timestep unless the error "
          "suggests otherwise)."
    )
    try:
        r = requests.post(
            f"{base_url}/api/chat",
            json={"model": model, "stream": False, "tools": [tool],
                  "messages": [{"role": "user", "content": msg}],
                  "options": {"temperature": 0.0}},
            timeout=30,
        )
        args = r.json()["message"]["tool_calls"][0]["function"]["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        v = int(args["timesteps_per_hour"])
        return v if 60 % v == 0 else None
    except Exception:
        return None


def rule_fix_timestep(digest: dict) -> int:
    """deterministic fallback: pick a valid timestep (a divisor of 60).

    copes with both message styles - the old 'requested number (n)' warning and the
    newer json-schema severe 'number_of_timesteps_per_hour ... expected number ...'.
    """
    valid = [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60]
    for s in digest["severe"] + digest["fatal"]:
        m = re.search(r"Requested number \((\d+)\)", s)
        if m:
            bad = int(m.group(1))
            return min(valid, key=lambda v: (abs(v - bad), v)) if bad not in valid else 6
    return 6  # a safe 10-minute timestep


def apply_timestep(idf_text: str, value: int) -> str:
    return re.sub(r"Timestep,\s*\d+\s*;", f"Timestep,{value};", idf_text, count=1)


def self_heal(idf_path: Path, epw: str, out_root: Path, base_url: str, model: str,
              progress=print) -> dict:
    """run it; if it dies, work out the fix from the .err, patch the idf, run again."""
    idf_path = Path(idf_path)
    out1 = out_root / "sc_attempt1"
    progress(f"[self-heal] run #1 with {idf_path.name} ...")
    rc1 = run_energyplus(str(idf_path), epw, str(out1))
    digest = parse_errors(out1 / "eplusout.err")
    if rc1 == 0 and not digest["fatal"]:
        return {"healed": False, "reason": "no error to fix", "rc": rc1}

    progress(f"[self-heal] detected failure. severe={digest['severe'][:1]}")
    # llm reads the error and suggests a fix; deterministic fallback if it can't
    fix = llm_diagnose_timestep(digest, base_url, model)
    source = "llm"
    if fix is None:
        fix, source = rule_fix_timestep(digest), "rule-fallback"
    progress(f"[self-heal] chosen timestep={fix} (via {source})")

    fixed_path = idf_path.with_name("self_healed.idf")
    fixed_path.write_text(apply_timestep(idf_path.read_text(encoding="latin-1"), fix),
                          encoding="latin-1")
    out2 = out_root / "sc_attempt2"
    progress(f"[self-heal] run #2 with {fixed_path.name} ...")
    rc2 = run_energyplus(str(fixed_path), epw, str(out2))
    digest2 = parse_errors(out2 / "eplusout.err")
    ok = rc2 == 0 and not digest2["fatal"]
    progress(f"[self-heal] result: {'success' if ok else 'still failing'} (rc={rc2})")
    return {
        "healed": ok,
        "fix_value": fix,
        "fix_source": source,
        "error_before": digest["severe"][:2] or digest["fatal"][:1],
        "fixed_idf": str(fixed_path),
        "rc_before": rc1,
        "rc_after": rc2,
    }
