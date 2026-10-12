"""The star measurement against frames whose answer is known.

    python tools/check_stars.py

Synthetic frames: Gaussian stars of a known width on noise, whose
flux-weighted half-flux radius is 1.253 sigma by arithmetic, in focus and
far out of it; a frame of burnt-out stars; and the shape of a focus sweep.
The real-frame check that led here is recorded in the numbers: on a 300 s
sub of M31 the old measurement read 9.5 px where ASTAP read 1.6, because
detection at a quarter scale kept only the brightest stars and measured
each across a box of wings. The same code now reads 1.59 on that sub.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrocontrol.imaging import stars                                  # noqa: E402

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(ok)


def frame(sigma, count=400, shape=(2000, 3000), seed=1, bright=False):
    rng = np.random.default_rng(seed)
    image = rng.normal(1000, 20, shape).astype(np.float32)
    reach = 40
    yy, xx = np.mgrid[-reach:reach + 1, -reach:reach + 1]
    profile = np.exp(-(yy ** 2 + xx ** 2) / (2 * sigma ** 2))
    for _ in range(count):
        cy = rng.integers(reach + 20, shape[0] - reach - 20)
        cx = rng.integers(reach + 20, shape[1] - reach - 20)
        amplitude = rng.uniform(300, 20000) * (1.5 / sigma) ** 2
        if bright:
            amplitude = rng.uniform(50000, 200000)
        image[cy - reach:cy + reach + 1, cx - reach:cx + reach + 1] += amplitude * profile
    return np.clip(image, 0, 65535)


# 1. In focus: sigma 1.5 px is a half-flux radius of 1.88.
started = time.monotonic()
measured = stars.measure(frame(1.5))
took = time.monotonic() - started
case("focused stars measure at their arithmetic half-flux radius",
     measured["hfr"] is not None and abs(measured["hfr"] - 1.88) < 0.1
     and measured["stars"] >= 300,
     f"hfr={measured['hfr']} stars={measured['stars']} in {took:.1f}s")

# 2. Far out of focus: sigma 6 is 7.5, and the sweep must still see it as larger.
far = stars.measure(frame(6.0))
case("defocused stars measure larger, near their arithmetic radius",
     far["hfr"] is not None and abs(far["hfr"] - 7.52) < 0.8 and far["hfr"] > 4 * measured["hfr"],
     f"hfr={far['hfr']} stars={far['stars']}")

# 3. A sweep: the radius grows monotonically either side of focus.
sweep = [stars.measure(frame(sigma, count=150, seed=3))["hfr"] for sigma in (1.0, 1.5, 2.5, 4.0, 6.0)]
case("a focus sweep reads monotonically",
     all(sweep[i] is not None and sweep[i] < sweep[i + 1] for i in range(len(sweep) - 1)),
     f"{[round(s, 2) if s else None for s in sweep]}")

# 4. Burnt-out stars are not measured; a frame of nothing else says so.
burnt = stars.measure(frame(1.5, count=60, bright=True))
case("stars past the linear range are left out",
     burnt["stars"] == 0 and burnt["hfr"] is None, f"{burnt}")

# 5. A frame with both: the burnt ones do not drag the median up.
mixed = frame(1.5, count=300, seed=5)
hot = frame(1.5, count=40, seed=6, bright=True) - 1000
mixed = np.clip(mixed + np.maximum(hot, 0), 0, 65535)
both = stars.measure(mixed)
case("burnt-out stars among good ones do not move the answer",
     both["hfr"] is not None and abs(both["hfr"] - 1.88) < 0.12, f"hfr={both['hfr']} stars={both['stars']}")

# 6. Positions come back in full-resolution pixels, brightest first.
image = frame(1.5, count=50, seed=9)
found = stars.detect(image)
y, x, _ = found[0]
patch = image[int(y) - 3:int(y) + 4, int(x) - 3:int(x) + 4]
case("detected positions land on stars at full resolution",
     len(found) >= 40 and float(patch.max()) > 1500, f"found={len(found)} peak={float(patch.max()):.0f}")

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
