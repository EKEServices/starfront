"""The COM executor against a driver call that never returns.

    python tools/check_com_executor.py

A ZWO camera that drops off the USB bus leaves the driver's StartExposure
sitting inside the SDK for good. The apartment thread it runs on is the only
thread the telescope's devices are called from, so everything queued behind
it - the status feed's property reads, the retry, the reconnect - waited its
own three minutes each, and the window was dead for twenty. These cases pin
down the way out: a caller that times out behind a call stuck for longer
than WEDGE_SECONDS declares the thread dead, everything waiting fails at once,
the channel gets a fresh thread, and a device opened on the dead one reports
itself disconnected instead of waiting again.

Needs pywin32 (the thread initialises COM), which Starfront needs anyway.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrocontrol.devices import ascom                           # noqa: E402
from astrocontrol.devices.base import DeviceError                # noqa: E402

if not ascom._HAVE_PYWIN32:
    print("SKIP  pywin32 is not installed")
    sys.exit(0)

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(ok)


ascom.WEDGE_SECONDS = 1.0
release = threading.Event()


def stuck():
    release.wait()          # the driver inside the SDK, with the camera gone
    return "finally"


# 1. A quick call behind nothing answers; a slow queue is not a wedge.
executor = ascom._com("test")
case("an ordinary call answers", executor.call(lambda: 21 * 2, label="Add") == 42)
try:
    executor.call(lambda: time.sleep(0.6), timeout=0.2, label="Nap")
    slow = None
except DeviceError as exc:
    slow = str(exc)
case("a call that is merely slow times out without killing the thread",
     slow == "no answer within 0.2s" and not executor.dead, f"{slow!r}")
time.sleep(0.6)

# 2. The stuck call. Two callers queue behind it: one short, one long.
started = time.monotonic()
try:
    executor.call(stuck, timeout=0.2, label="StartExposure")
except DeviceError as exc:
    first = str(exc)
case("the stuck call's own caller gets a plain timeout at first (not yet a minute)",
     first == "no answer within 0.2s" and not executor.dead, first)

waiters = {}


def wait_behind(name, timeout):
    try:
        executor.call(lambda: name, timeout=timeout, label=name)
        waiters[name] = "answered"
    except DeviceError as exc:
        waiters[name] = str(exc)


long_wait = threading.Thread(target=wait_behind, args=("ImageReady", 30.0), daemon=True)
long_wait.start()
time.sleep(1.2)                         # past WEDGE_SECONDS for the stuck call
try:
    executor.call(lambda: 1, timeout=0.2, label="CCDTemperature")
except DeviceError as exc:
    verdict = str(exc)
case("a caller timing out behind a call stuck for over the limit declares the thread dead",
     executor.dead and "StartExposure has been stuck" in verdict
     and "power-cycle" in verdict, verdict)
long_wait.join(3.0)
case("...and everything already waiting behind it fails at once, not after its own timeout",
     waiters.get("ImageReady", "") == verdict and time.monotonic() - started < 5.0,
     f"{waiters.get('ImageReady')!r} after {time.monotonic() - started:.1f}s")
try:
    executor.call(lambda: 1, label="Later")
    later = None
except DeviceError as exc:
    later = str(exc)
case("...and a call on the dead executor fails at once", later == verdict)

# 3. The channel gets a fresh thread, which works.
fresh = ascom._com("test")
case("the channel gets a fresh thread", fresh is not executor and not fresh.dead)
case("...which answers", fresh.call(lambda: "ok", label="Ping") == "ok")


# 4. A device opened on the dead thread says it is disconnected and does not wait.
class Wrapper(ascom._AscomDevice):
    _channel = "test"

    def __init__(self):
        self._connected = True
        self._executor = executor
        self._driver = None

    @property
    def connected(self):
        return self._connected


device = Wrapper()
started = time.monotonic()
value = device._get("Temperature", default="n/a")
try:
    device._invoke("StartExposure", 1.0, True)
    invoked = None
except DeviceError as exc:
    invoked = str(exc)
case("a device on the dead thread answers its status default and reports disconnected",
     value == "n/a" and device.connected is False and time.monotonic() - started < 1.0,
     f"value={value!r} connected={device.connected}")
case("...and a command on it says what happened and what to do",
     invoked is not None and invoked.startswith("StartExposure: the driver has stopped answering"),
     f"{invoked!r}")

release.set()
print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
