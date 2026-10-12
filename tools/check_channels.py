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


# 1. The table. A colour camera's data is its own band, whatever the filter;
# a mono camera's dual-band is a band of its own too.
for name, colour, want in (
        ("Ha", False, ["H"]), ("Ha 3nm", False, ["H"]), ("OIII", False, ["O"]), ("SII 3nm", False, ["S"]),
        ("Luminance", False, ["L"]), ("UV/IR cut", False, ["L"]), ("UV/IR cut", True, ["OSC-RGB"]),
        ("L", True, ["OSC-RGB"]), ("Red", False, ["R"]), ("RGB", False, ["OSC-RGB"]), ("Ha", True, ["OSC-Ha"]),
        ("L-eXtreme", False, ["Ha+OIII"]), ("Optolong L-eXtreme", True, ["OSC-Ha+OIII"]), ("L-Ultimate", True, ["OSC-Ha+OIII"]),
        ("ALP-T", True, ["OSC-Ha+OIII"]), ("Dual band 7nm", True, ["OSC-Ha+OIII"]), ("Askar D1", True, ["OSC-Ha+OIII"]),
        ("Askar D2", True, ["OSC-SII+OIII"]), ("Triad Ultra", True, ["OSC-Ha+OIII+SII"]), ("Quad band", True, ["OSC-Ha+OIII+SII"]),
        ("L-Pro", True, ["OSC-RGB"]), ("L-Pro", False, ["L"]), ("Diffraction grating", False, []), ("", False, [])):
    got = filters.channels(name, colour)
    case(f"{name or '(blank)'}{' on a colour camera' if colour else ''} delivers {want}", got == want, f"got {got}")
case("a band's spelling is fixed whatever the case typed",
     filters.normal_channel("osc-ha+oiii") == "OSC-Ha+OIII" and filters.normal_channel("h") == "H"
     and filters.normal_channel("Mystery") == "")
case("a band with an emission line in it is moon-tolerant",
     filters.is_narrowband("OSC-Ha+OIII") and filters.is_narrowband("S") and not filters.is_narrowband("OSC-RGB")
     and not filters.is_narrowband("O"))

# 2. A colour rig with a UV/IR cut and an L-eXtreme, read from the names.
osc = collab.RigProfile.read({
    "name": "OSC", "focalLength": 400.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "filters": {"UV/IR cut": None, "L-eXtreme": 7.0}, "colour": True,
    "exposures": {"UV/IR cut": 120, "L-eXtreme": 300}, "bortle": 2,
})
case("the profile reads each filter's band from its name",
     osc.delivers("L-eXtreme") == ["OSC-Ha+OIII"] and osc.delivers("UV/IR cut") == ["OSC-RGB"],
     f"{osc.channels}")
wants = collab.Requirements.read({"filters": {"H": 7.0, "O": 7.0, "S": 7.0, "R": None, "G": None, "B": None,
                                              "OSC-RGB": None, "OSC-Ha+OIII": 7.0}})
verdict = collab.compatibility(osc, wants)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("...and can take part in both OSC bands of a project that takes them, through which filters",
     verdict["ok"] and filters_check["ok"] and "OSC-RGB" in filters_check["detail"]
     and "OSC-Ha+OIII" in filters_check["detail"] and "L-eXtreme gives OSC-Ha+OIII" in filters_check["detail"],
     filters_check["detail"])
mono_only = collab.Requirements.read({"filters": {"H": 7.0, "O": 7.0, "R": None, "G": None, "B": None}})
verdict = collab.compatibility(osc, mono_only)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("a project listing mono channels only has nothing for a colour camera, and says who can change that",
     not verdict["ok"] and "takes no one-shot colour data" in filters_check["detail"], filters_check["detail"])
no_osc = collab.Requirements.read({"filters": {"OSC-RGB": None}, "acceptOscRgb": False})
verdict = collab.compatibility(osc, no_osc)
case("a project that switched OSC broadband off no longer wants the OSC-RGB it lists",
     not verdict["ok"] and "takes no one-shot colour data" in verdict["summary"], verdict["summary"])
none = collab.Requirements.read({"filters": {"OSC-RGB": None}, "acceptColour": False})
verdict = collab.compatibility(osc, none)
case("...and one that switched colour cameras off refuses one on the camera check",
     not verdict["ok"] and any(c["check"] == "camera" and c["ok"] is False for c in verdict["checks"]))

# 3. A project that wants narrower than the dual-band gives.
strict = collab.Requirements.read({"filters": {"OSC-Ha+OIII": 5.0}})
verdict = collab.compatibility(osc, strict)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("a 7 nm dual-band is refused by a 5 nm project, by name",
     not filters_check["ok"] and "L-eXtreme is 7 nm" in filters_check["detail"], filters_check["detail"])

# 4. A program that chose by hand overrides the name; the Bortle rule.
chosen = collab.RigProfile.read({
    "name": "Chosen", "focalLength": 400.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "filters": {"Mystery": 7.0}, "colour": True, "channels": {"Mystery": ["OSC-SII+OIII"]},
})
case("what the operator said a filter delivers wins over its name",
     chosen.delivers("Mystery") == ["OSC-SII+OIII"])
unknown = collab.RigProfile.read({"name": "x", "filters": {"Mystery": None}, "colour": True})
verdict = collab.compatibility(unknown, wants)
filters_check = next(c for c in verdict["checks"] if c["check"] == "filters")
case("a filter nobody can name is pointed at the filter settings",
     not filters_check["ok"] and "Mystery" in filters_check["detail"] and "delivers" in filters_check["detail"],
     filters_check["detail"])
dark = collab.Requirements.read({"filters": {"OSC-RGB": None}, "maxBortle": 3})
case("a Bortle 2 sky passes a project wanting Bortle 3 or darker",
     any(c["check"] == "sky" and c["ok"] for c in collab.compatibility(osc, dark)["checks"]))
city = collab.RigProfile.read({"name": "city", "filters": {"L": None}, "bortle": 7})
case("...a Bortle 7 sky does not, and is told",
     any(c["check"] == "sky" and c["ok"] is False and "Bortle 7" in c["detail"]
         for c in collab.compatibility(city, dark)["checks"]))
unrated = collab.RigProfile.read({"name": "unrated", "filters": {"L": None}})
case("...and an unrated sky is told what is wanted rather than refused",
     any(c["check"] == "sky" and c["ok"] is None for c in collab.compatibility(unrated, dark)["checks"]))

# 5. The judge: a night through the dual-band is one night in its own band.
night = collab.Contribution.read({"filterName": "L-eXtreme", "channels": ["OSC-Ha+OIII"], "frames": 10,
                                  "seconds": 3000, "exposure": 300, "bandpass": 7.0, "colour": True, "bortle": 2})
case("an OSC dual-band night is accepted by a project that takes OSC-Ha+OIII at 7 nm",
     collab.judge(night, wants)["accepted"], collab.judge(night, wants)["summary"])
case("...and refused by one wanting 5 nm, naming the filter",
     not collab.judge(night, strict)["accepted"] and "L-eXtreme" in collab.judge(night, strict)["summary"],
     collab.judge(night, strict)["summary"])
case("...and refused by one that lists mono channels only",
     not collab.judge(night, mono_only)["accepted"])
no_nb = collab.Requirements.read({"filters": {"OSC-Ha+OIII": None}, "acceptOscNarrowband": False})
case("...and refused by one that switched OSC narrowband off",
     not collab.judge(night, no_nb)["accepted"] and "does not take" in collab.judge(night, no_nb)["summary"])
bright = collab.Requirements.read({"filters": {"OSC-Ha+OIII": None}, "maxBortle": 1})
case("...and refused under a brighter sky than the project wants",
     not collab.judge(night, bright)["accepted"] and "Bortle 2" in collab.judge(night, bright)["summary"],
     collab.judge(night, bright)["summary"])

# 6. Dealing: the OSC bands are their own goals, kept apart from the mono channels.
cells = [{"ra": 10.0, "dec": 40.0, "width": 1.0, "height": 1.0}]
task_filters = [collab.FilterTask("L-eXtreme", 300.0, 10.0, ["OSC-Ha+OIII"]),
                collab.FilterTask("UV/IR cut", 120.0, 4.0, ["OSC-RGB"])]
goals = {"H": 10.0, "O": 10.0, "OSC-Ha+OIII": 10.0, "OSC-RGB": 4.0}
depth = {0: {"h": 9 * 3600.0, "o": 9 * 3600.0, "osc-ha+oiii": 2 * 3600.0, "osc-rgb": 4 * 3600.0}}
need = collab.night_need(task_filters, goals, depth, cells, {})
case("an OSC dual-band's need is its own band's, not the mono Ha and OIII's",
     abs(need["l-extreme"]["left"] - 8 * 3600.0) < 1 and abs(need["l-extreme"]["goal"] - 10 * 3600.0) < 1,
     f"{need}")
case("...and OSC broadband at its goal wants nothing", need["uv/ir cut"]["left"] == 0.0, f"{need['uv/ir cut']}")
chosen = collab.choose_filter(task_filters, goals, depth, cells, {}, 4.0)
case("on a mosaic the dual-band is chosen over the finished broadband", chosen.filter == "L-eXtreme")
moony = collab.choose_filter(task_filters, goals, {0: {}}, cells, {}, 4.0,
                             {"illumination": 0.95, "upFraction": 0.9})
case("under a bright Moon the OSC dual-band is the one to shoot", moony.filter == "L-eXtreme")

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
