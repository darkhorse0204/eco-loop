"""dump every actuator/variable the energyplus api exposes for our model.

this is also how the agent stays honest about what it can touch - it can read this
list to see what's actually sense-able and actuatable instead of assuming.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import eplus_paths  # noqa: E402

eplus_paths.ensure_on_path()
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
idf = str(REPO / "models/baseline.idf")
epw = str(REPO / "weather/USA_FL_Tampa.Intl.AP.722110_TMY3.epw")
out = str(REPO / "outputs/_probe")

api = EnergyPlusAPI()
state = api.state_manager.new_state()

dumped = {"done": False}


def cb(s):
    if dumped["done"]:
        return
    if not api.exchange.api_data_fully_ready(s):
        return
    csv = api.exchange.list_available_api_data_csv(s).decode("utf-8", "replace")
    Path(REPO / "outputs/_probe_api_data.csv").write_text(csv, encoding="utf-8")
    # just print the zone temperature control actuators + a couple of key variables
    for line in csv.splitlines():
        if "Zone Temperature Control" in line or "Zone People Occupant Count" in line:
            print(line)
    dumped["done"] = True


api.runtime.callback_begin_system_timestep_before_predictor(state, cb)
api.runtime.set_console_output_status(state, False)
api.runtime.run_energyplus(state, ["-w", epw, "-d", out, "-r", idf])
print("\nfull list written to outputs/_probe_api_data.csv")
