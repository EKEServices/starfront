"""Filters deliver channels: the table, and what the rules make of it.

    python tools/check_channels.py

A project wants channels - L, R, G, B, Ha, OIII, SII. A mono wheel's Ha
delivers Ha. A one-shot colour camera's clear filter delivers red, green
and blue in one sub, and an L-eXtreme in front of it delivers Ha and OIII
at once. These cases pin the name table down, then the compatibility and
the judge for a colour rig with a dual-band filter, which used to be told
it carried none of the wanted filters because nothing knew what an
"L-eXtreme" was.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrocontrol import collab, filters                                # noqa: E402

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(ok)


# 1. The table.
for name, colour, want in (
        ("Ha", False, ["H"]), ("Ha 3nm", False, ["H"]), ("OIII", False, ["O"]), ("SII 3nm", False, ["S"]),
        ("Luminance", False, ["L"]), ("UV/IR cut", False, ["L"]), ("UV/IR cut", True, ["R", "G", "B"]),
        ("L", True, ["R", "G", "B"]), ("Red", False, ["R"]), ("RGB", False, ["R", "G", "B"]),
        ("L-eXtreme", False, ["H", "O"]), ("Optolong L-eXtreme", True, ["H", "O"]), ("L-Ultimate", True, ["H", "O"]),
        ("ALP-T", True, ["H", "O"]), ("Dual band 7nm", True, ["H", "O"]), ("Askar D1", True, ["H", "O"]),
        ("Askar D2", True, ["S", "O"]), ("Triad Ultra", True, ["H", "O", "S"]), ("Quad band", True, ["H", "O", "S"]),
        ("L-Pro", True, ["R", "G", "B"]), ("L-Pro", False, ["L"]), ("Diffraction grating", False, []), ("", False, [])):
    got = filters.channels(name, colour)
    case(f"{name or '(blank)'}{' on a colour camera' if colour else ''} delivers {want}", got == want, f"got {got}")

# 2. A colour rig with a UV/IR cut and an L-eXtreme, read from the names.
osc = collab.RigProfile.read({
    "name": "OSC", "focalLength": 400.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "filters": {"UV/IR cut": None, "L-eXtreme": 7.0}, "colour": True,
    "exposures": {"UV/IR cut": 120, "L-eXtreme": 300},
})
case("the profile reads each filter's channels from its name",
     osc.delivers("L-eXtreme") == ["H", "O"] and osc.delivers("UV/IR cut") == ["R", "G", "B"],
     f"{osc.channels}")
wants = collab.Requirements.read({"filters": {"H": 7.0, "O": 7.0, "S": 7.0, "R": None, "G": None, "B": None}})
verdict = collab.compatibility(osc, wants)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("...and can take part in five of the project's six channels",
     verdict["ok"] and filters_check["ok"] and all(x in filters_check["detail"] for x in ("H", "O", "R", "G", "B"))
     and "no S" in filters_check["detail"], filters_check["detail"])

# 3. A project that wants narrower than the dual-band gives.
strict = collab.Requirements.read({"filters": {"H": 5.0, "O": 5.0}})
verdict = collab.compatibility(osc, strict)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("a 7 nm dual-band is refused by a 5 nm project, by name",
     not filters_check["ok"] and "L-eXtreme is 7 nm" in filters_check["detail"], filters_check["detail"])

# 4. A program that chose by hand overrides the name.
chosen = collab.RigProfile.read({
    "name": "Chosen", "focalLength": 400.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "filters": {"Mystery": 7.0}, "colour": True, "channels": {"Mystery": ["S", "O"]},
})
case("what the operator said a filter delivers wins over its name",
     chosen.delivers("Mystery") == ["S", "O"])
unknown = collab.RigProfile.read({"name": "x", "filters": {"Mystery": None}, "colour": True})
verdict = collab.compatibility(unknown, wants)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("a filter nobody can name is pointed at the filter settings",
     not filters_check["ok"] and "Mystery" in filters_check["detail"] and "delivers" in filters_check["detail"],
     filters_check["detail"])

# 5. The judge: a night through the dual-band carries both lines.
night = collab.Contribution.read({"filterName": "L-eXtreme", "channels": ["H", "O"], "frames": 10,
                                  "seconds": 3000, "exposure": 300, "bandpass": 7.0, "colour": True})
case("a dual-band night is accepted by a project wanting Ha and OIII at 7 nm",
     collab.judge(night, wants)["accepted"], collab.judge(night, wants)["summary"])
case("...and refused by one wanting 5 nm, naming the filter",
     not collab.judge(night, strict)["accepted"] and "L-eXtreme" in collab.judge(night, strict)["summary"],
     collab.judge(night, strict)["summary"])
only_s = collab.Requirements.read({"filters": {"S": None}})
case("...and refused by one that wants only SII",
     not collab.judge(night, only_s)["accepted"])

# 6. Dealing: a task's dual-band filter wants as much as its thinnest channel.
cells = [{"ra": 10.0, "dec": 40.0, "width": 1.0, "height": 1.0}]
task_filters = [collab.FilterTask("L-eXtreme", 300.0, 10.0, ["H", "O"]),
                collab.FilterTask("UV/IR cut", 120.0, 4.0, ["R", "G", "B"])]
goals = {"H": 10.0, "O": 10.0, "R": 4.0, "G": 4.0, "B": 4.0}
depth = {0: {"h": 9 * 3600.0, "o": 2 * 3600.0, "r": 4 * 3600.0, "g": 4 * 3600.0, "b": 4 * 3600.0}}
need = collab.night_need(task_filters, goals, depth, cells, {})
case("a dual-band's need is its thinnest channel's: OIII at two of ten hours",
     abs(need["l-extreme"]["left"] - 8 * 3600.0) < 1 and abs(need["l-extreme"]["goal"] - 10 * 3600.0) < 1,
     f"{need}")
case("...and a broadband filter whose three channels are all done wants nothing",
     need["uv/ir cut"]["left"] == 0.0, f"{need['uv/ir cut']}")
chosen = collab.choose_filter(task_filters, goals, depth, cells, {}, 4.0)
case("on a mosaic the dual-band is chosen over the finished broadband", chosen.filter == "L-eXtreme")

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
