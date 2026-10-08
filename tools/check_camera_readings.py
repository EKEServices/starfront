"""The cooler readings of an ASCOM camera, against a driver that misbehaves.

    python tools/check_camera_readings.py

Some drivers answer a property read made while the camera is reading out with
nonsense - a sensor at -2 C said to be at +1 C for a moment - and the status
poll asks every second. These checks drive the wrapper with a scripted driver
and make sure a lone wild reading is not believed, a real change is followed,
nothing is asked while the camera reads out, and a switch of the cooler is
seen at once.

No test framework, for the same reason as the other checks here.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrocontrol.devices import ascom                          # noqa: E402

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


class ScriptedDriver:
    """Answers property reads from a script, counting what was asked."""

    def __init__(self):
        self.values = {"CameraState": 0, "CCDTemperature": -2.0, "SetCCDTemperature": -5.0,
                       "CoolerOn": True, "CoolerPower": 100.0}
        self.asked = []

    def get(self, name):
        self.asked.append(name)
        return self.values[name]


def camera_with(driver):
    camera = ascom.AscomCamera.__new__(ascom.AscomCamera)
    camera._connected = True
    camera.can_cool = True
    camera._get = lambda name, default=None: driver.get(name) if name in driver.values else default
    camera._set = lambda name, value: driver.values.__setitem__(name, value)
    camera._require = lambda: None
    camera._forget_cooler()
    return camera


def tick(camera):
    """Age the cache so the next read really asks the driver."""
    cache = getattr(camera, "_cooler_cache", None)
    if cache is not None:
        cache["at"] = time.monotonic() - 10.0


driver = ScriptedDriver()
camera = camera_with(driver)

case("the first reading is what the driver says", camera.temperature == -2.0 and camera.setpoint == -5.0
     and camera.cooler_on is True and camera.cooler_power == 100.0)

asked_before = len(driver.asked)
camera.temperature; camera.setpoint; camera.cooler_on; camera.cooler_power
case("four properties within two seconds cost the driver nothing more",
     len(driver.asked) == asked_before, f"{len(driver.asked) - asked_before} extra reads")

# -- a lone wild reading -----------------------------------------------------
tick(camera)
driver.values["CCDTemperature"] = 1.0
case("a lone jump of three degrees is not believed", camera.temperature == -2.0)
tick(camera)
driver.values["CCDTemperature"] = -1.9
case("...and the next sane reading is", camera.temperature == -1.9)

# -- a real change ----------------------------------------------------------------
tick(camera)
driver.values["CCDTemperature"] = 8.0
first = camera.temperature
tick(camera)
driver.values["CCDTemperature"] = 8.2
second = camera.temperature
case("a change that is said twice is followed", first == -1.9 and second == 8.2, f"{first} then {second}")

# -- reading out -------------------------------------------------------------------
tick(camera)
driver.values["CameraState"] = 3
driver.values["CCDTemperature"] = 40.0
asked_before = len(driver.asked)
value = camera.temperature
case("nothing but the state is asked while the camera reads out, and the last reading stands",
     value == 8.2 and driver.asked[asked_before:] == ["CameraState"], str(driver.asked[asked_before:]))
tick(camera)
driver.values["CameraState"] = 0
driver.values["CCDTemperature"] = 8.1
case("...and reads resume afterwards", camera.temperature == 8.1)

# -- switching the cooler is seen at once -------------------------------------------
camera.set_cooler(False)
case("switching the cooler off shows at once", camera.cooler_on is False)
camera.set_setpoint(-10.0)
case("a new setpoint shows at once", camera.setpoint == -10.0)

# -- a driver that will not answer keeps the last reading ---------------------------
tick(camera)
del driver.values["CoolerPower"]
case("a property the driver stops answering keeps its last value", camera.cooler_power == 100.0)

# -- the status payload uses the same reading ---------------------------------------
driver.values["CoolerPower"] = 55.0
driver.values["PercentCompleted"] = 0
driver.values["LastExposureDuration"] = 0
driver.values["ImageReady"] = False
camera.sensor_width = camera.sensor_height = 100
camera.pixel_size_um = 3.76
camera.max_bin = 2
camera.gain = camera.offset = camera.binning = 1
camera.gain_min = camera.gain_max = camera.offset_min = camera.offset_max = 0
tick(camera)
status = camera.status()
case("status carries the cooler reading", status["temperature"] == 8.1 and status["coolerPower"] == 55.0
     and status["setpoint"] == -10.0, str({k: status[k] for k in ("temperature", "setpoint", "coolerOn", "coolerPower")}))

# -- a reading that has frozen ------------------------------------------------------
# Seen after a reconnect on a real night: the driver answered -2.0 C for sixteen
# minutes with the cooler reported off while the sensor warmed to +3, and
# started reading again the moment the setpoint was written. A sensor below
# room temperature with nothing cooling it cannot hold still.
frozen = ScriptedDriver()
frozen.values.update({"CCDTemperature": -2.0, "CoolerOn": False, "CoolerPower": 0.0})
written = []
stuck = camera_with(frozen)
stuck._set = lambda name, value: written.append((name, value))
stuck.temperature
tick(stuck); stuck.temperature
case("an identical reading for a few seconds is left alone", not written)
stuck._cooler_same_since = time.monotonic() - 200.0
tick(stuck); stuck.temperature
case("the same reading for three minutes with the cooler off re-sends the setpoint",
     written == [("SetCCDTemperature", -5.0)], str(written))
tick(stuck); stuck.temperature
case("...once, not every two seconds", len(written) == 1)
frozen.values["CCDTemperature"] = 2.4
tick(stuck); stuck.temperature
tick(stuck); frozen.values["CCDTemperature"] = 2.3; stuck.temperature
stuck._cooler_same_since = time.monotonic() - 200.0
tick(stuck); stuck.temperature
case("a reading that moves again is trusted again", stuck.temperature == 2.3 and len(written) == 1)

settled = ScriptedDriver()
settled.values.update({"CCDTemperature": -5.0, "CoolerOn": True, "CoolerPower": 31.0})
held = []
at_setpoint = camera_with(settled)
at_setpoint._set = lambda name, value: held.append((name, value))
at_setpoint.temperature
at_setpoint._cooler_same_since = time.monotonic() - 3600.0
tick(at_setpoint); at_setpoint.temperature
case("a cooler holding its setpoint for an hour is not nudged", not held)

warm = ScriptedDriver()
warm.values.update({"CCDTemperature": 22.0, "CoolerOn": False, "CoolerPower": 0.0})
touched = []
off = camera_with(warm)
off._set = lambda name, value: touched.append((name, value))
off.temperature
off._cooler_same_since = time.monotonic() - 3600.0
tick(off); off.temperature
case("a warm sensor with the cooler off is not nudged either", not touched)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
