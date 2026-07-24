"""demo: break the model on purpose, then let the agent heal it.

    python scripts/demo_self_correction.py

writes models/broken.idf with a bad timestep, runs it (energyplus fatals), and
shows the agent reading the .err, picking a fix with the llm, rewriting the idf,
and re-running clean - no human touching the code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import Config
from src.self_correction import apply_timestep, self_heal

cfg = Config.load()
REPO = Path(__file__).resolve().parents[1]

# 1) inject a bad timestep (0 is out of range -> energyplus fatals)
base = (REPO / "models/baseline.idf").read_text(encoding="latin-1")
broken = apply_timestep(base, 0)
broken_path = REPO / "models/broken.idf"
broken_path.write_text(broken, encoding="latin-1")
print("injected a bad `Timestep,0;` into models/broken.idf (energyplus will fatal)\n")

# 2) let the agent heal it
result = self_heal(
    broken_path,
    str(cfg.weather_path),
    cfg.output_dir / "self_correction",
    cfg.raw["llm"]["base_url"],
    cfg.raw["llm"]["model"],
)

print("\n================ self-correction report ================")
for k, v in result.items():
    print(f"  {k}: {v}")
print("=======================================================")
print("healed [ok]" if result.get("healed") else "not healed [fail]")
