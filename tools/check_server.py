"""Exercise the collaboration server and the agent that talks to it.

    python tools/check_server.py

Over real HTTP, against a real server on a real port, with a real SQLite file.
Not a test client: the thing being checked is a protocol between two machines,
and the parts most likely to be wrong — who is allowed to do what, what happens
when the same night is reported twice, what an agent does when the server is
not there — are exactly the parts a shortcut around the network would skip.

No test framework, for the same reason as the other checks here.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(tempfile.mkdtemp())
ADMIN = "test-admin-token"
os.environ["STARFRONT_DATA"] = str(DATA)
os.environ["STARFRONT_ADMIN_TOKEN"] = ADMIN
os.environ.setdefault("ASTRO_DATA_DIR", tempfile.mkdtemp())

from astrocontrol import collab                                  # noqa: E402
from astrocontrol.collabclient import CollabClient               # noqa: E402
from astrocontrol.config import Config                           # noqa: E402

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


# --------------------------------------------------------------- the server
def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"

# --------------------------------------------------------------- a fake Discord
# Enough of Discord to sign somebody in: the authorize page (which just sends
# the person straight back with a code), the token exchange, who-am-I, and
# the guild membership check. Which person "signs in" is chosen per test by
# the code they carry, so one fake serves a member with the role, a member
# without it, and a stranger.
DISCORD_PORT = free_port()
DISCORD_BASE = f"http://127.0.0.1:{DISCORD_PORT}"
GUILD = "guild-777"
ROLE = "role-imager"
PEOPLE = {
    "code-alice": {"id": "1001", "username": "alice", "global_name": "Alice",
                   "member": True, "roles": [ROLE]},
    "code-bob": {"id": "1002", "username": "bob", "global_name": "Bob",
                 "member": True, "roles": []},
    "code-mallory": {"id": "1003", "username": "mallory", "global_name": "Mallory",
                     "member": False, "roles": []},
    "code-carol": {"id": "1004", "username": "carol", "global_name": "Carol",
                   "member": True, "roles": []},
}


def fake_discord():
    import urllib.parse
    import uvicorn
    from fastapi import FastAPI, Header, Request
    from fastapi.responses import RedirectResponse
    fake = FastAPI()

    @fake.get("/oauth2/authorize")
    def authorize(redirect_uri: str, state: str, who: str = "code-alice"):
        return RedirectResponse(f"{redirect_uri}?code={who}&state={state}")

    @fake.post("/oauth2/token")
    async def token(request: Request):
        form = dict(urllib.parse.parse_qsl((await request.body()).decode()))
        code = form.get("code", "")
        if form.get("client_secret") != "shh" or code not in PEOPLE:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="bad code")
        return {"access_token": f"bearer-{code}", "token_type": "Bearer"}

    def person(authorization: str):
        return PEOPLE.get(authorization.replace("Bearer bearer-", ""))

    @fake.get("/users/@me")
    def me(authorization: str = Header(default="")):
        p = person(authorization)
        return {"id": p["id"], "username": p["username"],
                "global_name": p["global_name"], "avatar": ""}

    @fake.get("/users/@me/guilds/{guild}/member")
    def member(guild: str, authorization: str = Header(default="")):
        p = person(authorization)
        if guild != GUILD or not p["member"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Unknown Guild")
        return {"roles": p["roles"], "nick": None}

    uvicorn.run(fake, host="127.0.0.1", port=DISCORD_PORT, log_level="error")


threading.Thread(target=fake_discord, daemon=True).start()

os.environ["STARFRONT_DISCORD_CLIENT_ID"] = "app-1"
os.environ["STARFRONT_DISCORD_CLIENT_SECRET"] = "shh"
os.environ["STARFRONT_DISCORD_GUILD"] = GUILD
# Under the name the setting had before the server was called Starfront: a
# discord.env written then must still be read.
os.environ["ASTROCOLLAB_DISCORD_ROLE"] = ROLE
os.environ["STARFRONT_DISCORD_OWNERS"] = "1001"     # Alice runs the server
os.environ["STARFRONT_PUBLIC_URL"] = BASE
os.environ["STARFRONT_DISCORD_API"] = DISCORD_BASE
os.environ["STARFRONT_DISCORD_AUTHORIZE"] = f"{DISCORD_BASE}/oauth2/authorize"

# A folder of N.I.N.A. plugin releases for the server to offer: one manifest
# and the zip it points at, plus a file that is not a release at all.
NINA_DIR = Path(tempfile.mkdtemp())
os.environ["STARFRONT_NINA_DIR"] = str(NINA_DIR)
(NINA_DIR / "Starfront.Collab.1.0.0.0.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
(NINA_DIR / "Starfront.Collab.1.0.0.0.manifest.json").write_text(json.dumps({
    "Name": "Starfront Collaboration", "Identifier": "896B4E28-7CEB-4731-BB97-8BADE7460268",
    "Version": {"Major": "1", "Minor": "0", "Patch": "0", "Build": "0"},
    "Author": "test", "Repository": "https://example.org", "License": "MPL-2.0",
    "LicenseURL": "https://www.mozilla.org/en-US/MPL/2.0/",
    "MinimumApplicationVersion": {"Major": "3", "Minor": "2", "Patch": "0", "Build": "9001"},
    "Descriptions": {"ShortDescription": "test"},
    "Installer": {"URL": f"{BASE}/nina/download/Starfront.Collab.1.0.0.0.zip", "Type": "ARCHIVE",
                  "Checksum": "00", "ChecksumType": "SHA256"}}), encoding="utf-8")
(NINA_DIR / "notes.txt").write_text("not a release", encoding="utf-8")
(NINA_DIR / "CHANGELOG.md").write_text("# Change log\n\n## 1.0.0.0\n", encoding="utf-8")
(NINA_DIR / "Starfront Collaboration for NINA.pdf").write_bytes(b"%PDF-1.4\n%test\n")
# The installers the server offers at /download: two versions, where the
# newer one sorts first only by number, and a file that is not an installer.
DOWNLOAD_DIR = Path(tempfile.mkdtemp())
os.environ["STARFRONT_DOWNLOAD_DIR"] = str(DOWNLOAD_DIR)
(DOWNLOAD_DIR / "Starfront-Setup-0.2.9.exe").write_bytes(b"MZ-old" + b"\x00" * 10)
(DOWNLOAD_DIR / "Starfront-Setup-0.2.35.exe").write_bytes(b"MZ-new" + b"\x00" * 10)
(DOWNLOAD_DIR / "secrets.txt").write_text("not for anybody", encoding="utf-8")
# An older release left behind in the folder: the feed offers only the newest.
(NINA_DIR / "Starfront.Collab.0.9.0.0.manifest.json").write_text(json.dumps({
    "Name": "Starfront Collaboration", "Identifier": "896B4E28-7CEB-4731-BB97-8BADE7460268",
    "Version": {"Major": "0", "Minor": "9", "Patch": "0", "Build": "0"},
    "Author": "test", "Repository": "https://example.org", "License": "MPL-2.0",
    "LicenseURL": "https://www.mozilla.org/en-US/MPL/2.0/",
    "MinimumApplicationVersion": {"Major": "3", "Minor": "2", "Patch": "0", "Build": "9001"},
    "Descriptions": {"ShortDescription": "test"},
    "Installer": {"URL": f"{BASE}/nina/download/Starfront.Collab.0.9.0.0.zip", "Type": "ARCHIVE",
                  "Checksum": "00", "ChecksumType": "SHA256"}}), encoding="utf-8")


# The database the server opens is one from the day files were lights only:
# the files table without its kind column, keyed on the name alone. The
# server must bring it up to date at start rather than fail to start - which
# it did once, with the new index asked for before the column existed.
import sqlite3                                                   # noqa: E402
_old = sqlite3.connect(str(DATA / "collab.sqlite"))
_old.executescript("""
CREATE TABLE files (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, user TEXT NOT NULL,
    name TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0,
    received REAL NOT NULL, telescope TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX files_once ON files (project, user, name);
""")
_old.commit()
_old.close()


def serve():
    import uvicorn
    from server.app import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")


threading.Thread(target=serve, daemon=True).start()

for _ in range(100):
    try:
        urllib.request.urlopen(f"{BASE}/api/v1/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.2)
else:
    print("FAIL  the server did not start")
    sys.exit(1)


def call(method, path, body=None, token=None, expect=200):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode() or "{}")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


status, health = call("GET", "/api/v1/health")
case("the server answers", status == 200 and health["ok"])
case("and says which protocol it speaks", health["protocol"] == collab.PROTOCOL)

# ------------------------------------------------------------ who may do what
status, _ = call("POST", "/api/v1/agents", {"name": "nope"})
case("enrolling a telescope needs the coordinator's token", status == 401)

status, made = call("POST", "/api/v1/agents",
                    {"name": "Telescope 1", "owner": "bray"}, token=ADMIN)
case("the coordinator can enrol one", status == 200 and made["token"])
AGENT_TOKEN = made["token"]
AGENT_ID = made["agent"]["id"]
case("and the token is handed back exactly once",
     "token" not in made["agent"])

status, _ = call("GET", "/api/v1/agents", token=AGENT_TOKEN)
case("a telescope's token cannot administer the server", status == 401)

status, _ = call("POST", "/api/v1/agent/hello", {"protocol": 1}, token="rubbish")
case("an unknown token is refused", status == 401)

# ------------------------------------------------------ signing in with Discord
print("\n-- signing in with Discord --")


def sign_in(who):
    """The device-code flow end to end, as Starfront and a browser would do it.

    Starfront asks for a code; the browser opens the start page, is sent to
    Discord, comes back to the callback; Starfront's poll then hands over the
    user token. The fake Discord signs in whoever `who` names.
    """
    _, begun = call("POST", "/api/v1/auth/login")
    code = begun["code"]
    # The browser: follow the redirects by hand, because urllib would follow
    # them for us and hide what each hop said.
    import urllib.parse
    request = urllib.request.Request(begun["url"], method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        opener.open(request, timeout=10)
        raise AssertionError("expected a redirect to Discord")
    except urllib.error.HTTPError as hop:
        to_discord = hop.headers.get("Location")
    # Pick who signs in, the way a real person picks by logging into Discord.
    to_discord += f"&who={who}"
    try:
        opener.open(urllib.request.Request(to_discord, method="GET"), timeout=10)
        raise AssertionError("expected a redirect back from Discord")
    except urllib.error.HTTPError as hop:
        back = hop.headers.get("Location")
    try:
        with urllib.request.urlopen(back, timeout=10) as page:
            landed = page.status, page.read().decode()
    except urllib.error.HTTPError as exc:
        landed = exc.code, exc.read().decode()
    _, polled = call("GET", f"/api/v1/auth/poll?code={code}")
    return begun, to_discord, landed, polled


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


status, info = call("GET", "/api/v1/auth")
case("the server says Discord sign-in is on, and no role is needed to start one",
     status == 200 and info["discord"] and info["roleRequired"] is False)

begun, to_discord, landed, polled = sign_in("code-alice")
case("a sign-in begins with a code and a page to open",
     begun["code"] and begun["url"].startswith(BASE + "/auth/discord/start"))
case("...the page sends the browser to Discord asking only who and which guild",
     "scope=identify+guilds.members.read" in to_discord
     and "client_id=app-1" in to_discord
     and f"state={begun['code']}" in to_discord, to_discord[:120])
case("...Discord sends them back and the page says who they are",
     landed[0] == 200 and "Signed in as Alice" in landed[1])
case("...and Starfront's poll is handed the token, once",
     polled["state"] == "done" and polled["token"]
     and polled["user"]["name"] == "Alice" and polled["user"]["canStart"] is True,
     str({k: v for k, v in polled.items() if k != "token"}))
ALICE = polled["token"]
_, again = call("GET", f"/api/v1/auth/poll?code={begun['code']}")
case("...polling the same code again gets nothing", again["state"] == "claimed")

_, me = call("GET", "/api/v1/auth/me", token=ALICE)
case("the token says who it is, and that she may start collaborations",
     me["name"] == "Alice" and me["canStart"] is True)
case("...and that she owns the server, by her Discord id, with no token pasted",
     me["admin"] is True and polled["user"]["admin"] is True)

_, _, _, polled = sign_in("code-bob")
BOB = polled["token"]
case("a member without the customer role signs in too, and may start one: being in the server is the gate",
     polled["state"] == "done" and polled["user"]["canStart"] is True)

_, _, landed, polled = sign_in("code-mallory")
case("somebody not in the Discord server is turned away, and told so",
     landed[0] == 403 and "not a member" in landed[1] and polled["state"] == "pending",
     f"{landed[0]}")

# -------------------------------------------------------- who may do what, now
print("\n-- what a member may do --")

status, mine = call("POST", "/api/v1/agents", {"name": "Alice's rig"}, token=ALICE)
case("a member can enrol their own telescope", status == 200 and mine["token"])
case("...and it is theirs", mine["agent"]["owner_id"] == "1001"
     and mine["agent"]["owner"] == "Alice")
status, listing = call("GET", "/api/v1/agents", token=BOB)
case("...a member sees their own telescopes and nobody else's",
     status == 200 and listing["agents"] == [])
_, everything = call("GET", "/api/v1/agents", token=ALICE)
case("...while an owner sees them all", len(everything["agents"]) >= 2)

status, started = call("POST", "/api/v1/projects", {
    "name": "Alice's Veil", "region": {"ra": 313.0, "dec": 31.0, "width": 3.0,
                                       "height": 3.0}}, token=ALICE)
case("a member with the role can start a collaboration",
     status == 200 and started["project"]["owner_id"] == "1001"
     and started["project"]["coordinator"] == "Alice")
VEIL = started["project"]["id"]
_, browse = call("GET", "/api/v1/agent/projects", token=AGENT_TOKEN)
case("...and everybody browsing sees whose it is",
     next(p for p in browse["projects"] if p["id"] == VEIL)["ownerId"] == "1001")

status, bobs = call("POST", "/api/v1/projects", {
    "name": "Bob's", "region": {"ra": 0, "dec": 0, "width": 1, "height": 1}},
    token=BOB)
case("a member without the role can start one too", status == 200
     and bobs["project"]["owner_id"] == "1002" and bobs["project"]["coordinator"] == "Bob",
     bobs.get("detail") or bobs["project"]["coordinator"])
status, _ = call("PUT", f"/api/v1/projects/{bobs['project']['id']}", {"status": "closed"}, token=BOB)
case("...and close it again", status == 200)

# The lookup a program with no catalog of its own starts a collaboration from.
status, found = call("GET", "/api/v1/lookup?name=m31", token=BOB)
case("a signed-in member can look an object up by name, in degrees",
     status == 200 and found["results"] and found["results"][0]["id"] == "M31"
     and abs(found["results"][0]["ra"] - 10.68) < 0.1
     and abs(found["results"][0]["dec"] - 41.27) < 0.1
     and found["results"][0]["sizeArcmin"] == 178,
     str(found)[:160])
status, _ = call("GET", "/api/v1/lookup?name=m31", token=AGENT_TOKEN)
case("...a telescope's token cannot", status == 401)

status, _ = call("PUT", f"/api/v1/projects/{VEIL}", {"notes": "hi"}, token=BOB)
case("somebody else cannot change Alice's collaboration", status == 403)
status, _ = call("PUT", f"/api/v1/projects/{VEIL}", {"notes": "mine"}, token=ALICE)
case("Alice can", status == 200)
status, _ = call("PUT", f"/api/v1/projects/{VEIL}", {"notes": "owner"}, token=ADMIN)
case("...and so can the admin token", status == 200)
status, _ = call("PUT", f"/api/v1/projects/{VEIL}", {"notes": "x"}, token=AGENT_TOKEN)
case("a telescope's token still cannot", status == 401)

status, _ = call("POST", "/api/v1/auth/logout", token=BOB)
status2, _ = call("GET", "/api/v1/auth/me", token=BOB)
case("signing out stops the token working", status == 200 and status2 == 401)

# ------------------------------------------------------------------- hello
PROFILE = {
    "name": "Telescope 1", "focalLength": 530.0, "pixelSize": 3.76,
    "sensorWidth": 6248, "sensorHeight": 4176, "binning": 1,
    "filters": {"Ha": 3.0, "OIII": 3.0, "L": None},
    "typicalHfr": 2.4, "typicalGuideRms": 0.6,
}
status, hello = call("POST", "/api/v1/agent/hello",
                     {"protocol": 1, "profile": PROFILE}, token=AGENT_TOKEN)
case("a telescope can say hello", status == 200 and hello["agent"] == AGENT_ID)

status, _ = call("POST", "/api/v1/agent/hello", {"protocol": 99}, token=AGENT_TOKEN)
case("an agent from the future is refused rather than misunderstood",
     status == 409)

# ------------------------------------------------------------- no task yet
status, answer = call("GET", "/api/v1/agent/task", token=AGENT_TOKEN)
case("with nothing assigned there is no task", status == 200
     and answer["task"] is None)

# ----------------------------------------------------------------- a project
status, project = call("POST", "/api/v1/projects", {
    "name": "Cygnus Wall deep",
    "region": {"ra": 313.0, "dec": 44.5, "width": 2.0, "height": 1.5,
               "rotation": 0.0},
    "requirements": {"maxScale": 2.5, "maxHfr": 3.5, "maxGuideRms": 1.2,
                     "filters": {"Ha": 5.0, "OIII": 5.0},
                     "minExposure": 120},
    "goals": {"Ha": 8.0, "OIII": 8.0},
}, token=ADMIN)
case("a coordinator can start a collaboration", status == 200)
PROJECT = project["project"]["id"]

status, listing = call("GET", "/api/v1/projects")
case("and anyone can browse it — that is how people find one to join",
     status == 200 and any(p["id"] == PROJECT for p in listing["projects"]))

# ------------------------------------------------------------------ a task
status, made = call("POST", f"/api/v1/projects/{PROJECT}/tasks", {
    "agent": AGENT_ID,
    "region": {"ra": 313.2, "dec": 44.6, "width": 0.6, "height": 0.5},
    "filters": [{"filter": "Ha", "exposure": 600, "hours": 4.0}],
    "note": "north-east chunk",
}, token=ADMIN)
case("a chunk of sky can be delegated to a telescope", status == 200)
TASK = made["task"]["id"]
case("and it arrives offered rather than started",
     made["task"]["state"] == "offered")

status, answer = call("GET", "/api/v1/agent/task", token=AGENT_TOKEN)
case("the telescope is given it when it asks",
     status == 200 and answer["task"]["id"] == TASK)
case("along with what the project will accept",
     answer["requirements"]["maxHfr"] == 3.5)
case("and how many frames that is",
     collab.Task.read(answer["task"]).filters[0].frames() == 24,
     str(collab.Task.read(answer["task"]).filters[0].frames()))

status, answer = call("POST", f"/api/v1/agent/task/{TASK}",
                      {"state": "accepted"}, token=AGENT_TOKEN)
case("the operator can accept it", status == 200
     and answer["task"]["state"] == "accepted")
case("and accepting moves the version on, so a poll notices",
     answer["task"]["version"] == 2)

# A telescope must not be able to touch another's work.
status, other = call("POST", "/api/v1/agents", {"name": "Telescope 2"}, token=ADMIN)
OTHER_TOKEN = other["token"]
status, _ = call("POST", f"/api/v1/agent/task/{TASK}", {"state": "complete"},
                 token=OTHER_TOKEN)
case("one telescope cannot alter another's task", status == 404)

status, answer = call("GET", "/api/v1/agent/task", token=OTHER_TOKEN)
case("nor see it", answer["task"] is None)

# ----------------------------------------------------------- reporting a night
GOOD = {
    "task": TASK, "night": "2026-09-17", "filterName": "Ha",
    "frames": 24, "seconds": 14400, "exposure": 600,
    "footprint": {"ra": 313.2, "dec": 44.6, "width": 0.6, "height": 0.5},
    "scale": 1.46, "hfr": 2.6, "guideRms": 0.7, "bandpass": 3.0,
}
status, answer = call("POST", "/api/v1/agent/report",
                      {"contributions": [GOOD]}, token=AGENT_TOKEN)
case("a good night is accepted", status == 200
     and answer["recorded"][0]["accepted"], str(answer["recorded"][0]["verdict"]))

# The same night reported twice must not count twice — an agent retrying after
# a dropped connection is the normal case, not an error.
status, again = call("POST", "/api/v1/agent/report",
                     {"contributions": [GOOD]}, token=AGENT_TOKEN)
case("reporting it again does not count it twice",
     again["recorded"][0]["duplicate"] is True)

status, project = call("GET", f"/api/v1/projects/{PROJECT}")
case("and the ledger holds one row for it",
     len(project["contributions"]) == 1, str(len(project["contributions"])))

BAD = {**GOOD, "night": "2026-09-18", "hfr": 5.2, "guideRms": 2.4}
status, answer = call("POST", "/api/v1/agent/report",
                      {"contributions": [BAD]}, token=AGENT_TOKEN)
verdict = answer["recorded"][0]
case("a night outside the rules is rejected, with reasons",
     not verdict["accepted"] and len(verdict["verdict"]["reasons"]) == 2,
     verdict["verdict"]["summary"])

WIDE = {**GOOD, "night": "2026-09-19", "bandpass": 7.0}
status, answer = call("POST", "/api/v1/agent/report",
                      {"contributions": [WIDE]}, token=AGENT_TOKEN)
case("a filter too wide for the project is rejected",
     not answer["recorded"][0]["accepted"],
     answer["recorded"][0]["verdict"]["summary"])

# The coordinator has the last word.
rejected = [row for row in call("GET", f"/api/v1/projects/{PROJECT}")[1]["contributions"]
            if not row["accepted"]][0]
status, answer = call(
    "POST",
    f"/api/v1/contributions/{rejected['id']}/verdict?accepted=true"
    "&reason=only+data+on+that+sky",
    token=ADMIN)
case("a coordinator can overrule a rejection",
     status == 200 and answer["contribution"]["accepted"]
     and answer["contribution"]["overridden"])

# -------------------------------------------------------------- the agent side
class FakeManager:
    def __init__(self, devices):
        self.devices = devices

    def get(self, kind):
        return self.devices.get(kind)


class FakeRig:
    def __init__(self, config):
        self.id = "rig1"
        self.name = "Telescope 1"
        self.config = config
        self.manager = FakeManager({})


class FakeRigs:
    def __init__(self, rig):
        self.master = rig
        self.all = [rig]
        self.lines = []

    def log(self, message, level="info"):
        self.lines.append((level, message))


class FakeTargets:
    def listing(self):
        # Two nights of HFR in *pixels*, which is what the logs actually hold.
        return [{"integration": {"log": [
            {"hfrSum": 2.0 * 30, "hfrCount": 30},
            {"hfrSum": 2.4 * 20, "hfrCount": 20},
        ]}}]


config = Config(Path(tempfile.mkdtemp()) / "settings.json")
config.update("optics", {"focalLength": 530.0, "pixelSize": 3.76,
                         "sensorWidth": 6248, "sensorHeight": 4176})
config.update("camera", {"filterNames": ["L", "Ha", "OIII"],
                         "filterBandpass": {"Ha": 3.0, "OIII": 3.0}})
config.update("collab", {"enabled": True, "serverUrl": BASE,
                         "token": AGENT_TOKEN})
rigs = FakeRigs(FakeRig(config))
client = CollabClient(rigs, config, FakeTargets(), log=rigs.log)

profile = client.profile()
case("the agent works out its own image scale",
     abs(profile["scale"] - 1.4636) < 0.01, f"{profile['scale']}")
case("and its field of view",
     abs(profile["field"][0] - 2.54) < 0.05, f"{profile['field']}")
# Two nights at 2.0 and 2.4 px: a median of 2.2 px, which at this plate scale
# is 3.22 arcseconds. The conversion is the point — 2.2 px means nothing to a
# project, and comparing it directly against an arcsecond limit would call this
# rig four times better than it is.
case("and reports star size in arcseconds, not pixels",
     abs(profile["typicalHfr"] - 2.2 * 1.4636) < 0.05,
     f"{profile['typicalHfr']}\" from a median of 2.2 px")

task = client.poll()
case("the agent picks the task up", task and task["id"] == TASK)
case("and knows whether it can actually do it",
     client.status()["compatibility"]["ok"] is True,
     client.status()["compatibility"]["summary"])

# The near miss is the case worth having: 3.22" passes a 3.5" limit and fails a
# 3.0" one, and the difference is entirely in the arcsecond conversion.
tight = collab.compatibility(
    collab.RigProfile.read(profile),
    collab.Requirements.read({"maxHfr": 3.0, "filters": {"Ha": 5.0}}))
case("and a rig that is close but not close enough is told so",
     not tight["ok"], tight["summary"])

# A rig that cannot meet the rules should be told so, in terms it can act on.
narrow = Config(Path(tempfile.mkdtemp()) / "settings.json")
narrow.update("optics", {"focalLength": 130.0, "pixelSize": 9.0,
                         "sensorWidth": 1000, "sensorHeight": 1000})
narrow.update("camera", {"filterNames": ["Ha"], "filterBandpass": {"Ha": 12.0}})
verdict = collab.compatibility(
    collab.RigProfile.read({
        "focalLength": 130.0, "pixelSize": 9.0, "sensorWidth": 1000,
        "sensorHeight": 1000, "filters": {"Ha": 12.0}, "typicalHfr": 2.0}),
    collab.Requirements.read({"maxScale": 2.5, "filters": {"Ha": 5.0}}))
case("a rig that cannot contribute is told which part fails",
     not verdict["ok"] and len(verdict["checks"]) >= 2, verdict["summary"])

# Offline is a normal state for a remote rig, not a fault.
config.update("collab", {"serverUrl": "http://127.0.0.1:1"})
try:
    client.poll()
    raised = False
except Exception:
    raised = True
case("an unreachable server raises rather than corrupting the task", raised)
case("and the task already in hand is kept",
     client.status()["task"]["id"] == TASK)
config.update("collab", {"serverUrl": BASE})

# Reporting from the agent side, end to end.
answer = client.report([{
    "task": TASK, "night": "2026-09-20", "filterName": "OIII",
    "frames": 20, "seconds": 12000, "exposure": 600,
    "scale": 1.46, "hfr": 2.5, "guideRms": 0.8, "bandpass": 3.0,
}])
case("the agent can report a night and hear the verdict",
     answer["recorded"][0]["accepted"] is True,
     answer["recorded"][0]["verdict"]["summary"])

client.respond("complete")
case("and mark the task finished",
     client.status()["task"]["state"] == "complete")

# --------------------------------------------- reporting what was really shot
print("\n-- panels reported as they are shot --")

# The program tallies every sub against the panel it landed on, and the client
# sends those tallies, per night and per panel, on each poll. What has to be
# true: what was shot is what is reported, with the panel's own footprint; a
# night sent once is not sent again; and a night that grows after it was sent
# is sent again with the larger figure, which the server takes as the record.
from astrocontrol.targets import TargetStore                        # noqa: E402

store_ = TargetStore(Path(tempfile.mkdtemp()) / "targets.json")
mosaic = store_.create("Cygnus wall", 20.9, 44.6, 0.0, 2.3, 1.5, rows=2,
                       columns=2, collab={"project": PROJECT, "task": TASK,
                                          "version": 1, "server": BASE})
store_.add_panel_integration(mosaic["id"], "2026-09-21", 1, "Ha", 600.0, hfr=2.0)
store_.add_panel_integration(mosaic["id"], "2026-09-21", 1, "Ha", 600.0, hfr=2.4)
store_.add_panel_integration(mosaic["id"], "2026-09-21", 3, "OIII", 600.0, hfr=2.2)
pending = store_.unreported_panels(mosaic["id"])
case("subs are tallied against the panel they landed on",
     sorted((row["panel"], row["filter"], row["frames"]) for row in pending)
     == [(1, "Ha", 2), (3, "OIII", 1)],
     str([(row["panel"], row["filter"], row["frames"]) for row in pending]))

reporter = CollabClient(rigs, config, store_, log=rigs.log)
sent = reporter.report_pending()
case("the client reports every panel not yet sent", sent == 2, f"{sent} sent")
case("...and sends nothing the second time", reporter.report_pending() == 0)

_, ledger = call("GET", f"/api/v1/projects/{PROJECT}")
mine_rows = [row for row in ledger["contributions"]
             if row["night"] == "2026-09-21"]
case("the server holds one row per panel and filter for the night",
     sorted((r["payload"]["panel"], r["payload"]["filterName"], r["payload"]["frames"])
            for r in mine_rows) == [("1", "H", 2), ("3", "O", 1)],
     str([(r["payload"]["panel"], r["payload"]["filterName"]) for r in mine_rows]))
panel_row = next(r for r in mine_rows if r["payload"]["panel"] == "1")
panel_one = next(p for p in store_.get(mosaic["id"])["panels"] if p["index"] == 1)
case("...each with the panel's own footprint, not the mosaic's centre",
     abs(panel_row["payload"]["footprint"]["ra"] - panel_one["ra"] * 15.0) < 1e-6
     and abs(panel_row["payload"]["footprint"]["dec"] - panel_one["dec"]) < 1e-6,
     f'{panel_row["payload"]["footprint"]["ra"]:.3f}, '
     f'{panel_row["payload"]["footprint"]["dec"]:.3f}')
case("...star size in arcseconds, from the pixels the log holds",
     abs(panel_row["payload"]["hfr"] - 2.2 * 1.4636) < 0.05,
     f'{panel_row["payload"]["hfr"]:.2f}"')

# More frames on a panel already sent: sent again, and the larger figure wins.
store_.add_panel_integration(mosaic["id"], "2026-09-21", 1, "Ha", 600.0, hfr=2.0)
case("a panel that grows after it was sent is sent again",
     reporter.report_pending() == 1)
_, ledger = call("GET", f"/api/v1/projects/{PROJECT}")
grown = next(r for r in ledger["contributions"]
             if r["night"] == "2026-09-21" and r["payload"]["panel"] == "1")
case("...and the server keeps one row, at the larger figure",
     grown["payload"]["frames"] == 3 and grown["seconds"] == 1800.0,
     f'{grown["payload"]["frames"]} frames, {grown["seconds"]}s')

# --------------------------------------------------------- unverified is not ok
partial = collab.judge(
    collab.Contribution.read({"filterName": "Ha", "exposure": 600,
                              "bandpass": 3.0, "seconds": 3600}),
    collab.Requirements.read({"maxHfr": 3.0, "filters": {"Ha": 5.0},
                              "maxScale": 2.5}))
case("a night with nothing measured is accepted but flagged as unverified",
     partial["accepted"] and len(partial["unverified"]) == 2,
     partial["summary"])

# ==================================================================== joining
print("\n-- joining a mosaic: the whole of it, shared out --")

# The bug this pins down: a rig joining a forty-panel mosaic was handed one
# cell. Two rigs with different cameras join a wide project; the first, alone,
# holds all of it; when the second arrives the first's share shrinks and its
# version moves, which is how its program finds out.
def enrol(name, focal, width, height, hours):
    _, made = call("POST", "/api/v1/agents", {"name": name}, token=ADMIN)
    call("POST", "/api/v1/agent/hello", {
        "protocol": collab.PROTOCOL,
        "profile": {"name": name, "focalLength": focal, "pixelSize": 3.76,
                    "sensorWidth": width, "sensorHeight": height, "binning": 1,
                    "filters": {"Ha": 3.0}, "hoursPerNight": hours,
                    # field is what the server sizes cells by
                    "scale": 206.265 * 3.76 / focal,
                    "field": [206.265 * 3.76 / focal * width / 3600.0,
                              206.265 * 3.76 / focal * height / 3600.0]}},
        token=made["token"])
    return made["token"], made["agent"]["id"]


WIDE_TOKEN, WIDE_ID = enrol("wide", 389.0, 9576, 6388, 6.0)
NARROW_TOKEN, NARROW_ID = enrol("narrow", 1000.0, 6000, 4000, 3.0)

_, orion = call("POST", "/api/v1/projects", {
    "name": "Orion wide", "region": {"ra": 84.0, "dec": 0.0,
                                     "width": 16.0, "height": 8.0},
    "requirements": {"filters": {"Ha": 3.0}}, "goals": {"Ha": 10.0}},
    token=ADMIN)
ORION = orion["project"]["id"]

status, joined = call("POST", f"/api/v1/agent/projects/{ORION}/join", {},
                      token=WIDE_TOKEN)
task = joined["task"]
case("joining hands back the whole project's sky, not one cell",
     status == 200 and abs(task["region"]["width"] - 16.0) < 1e-6,
     f'{task["region"]["width"]} x {task["region"]["height"]} degrees')
case("...tiled with this rig's own camera",
     len(task["cells"]) > 4, f'{len(task["cells"])} cells')


def held(token, night):
    """What a rig is told to shoot on a given night, on the Orion project."""
    _, answer = call("GET", f"/api/v1/agent/task?night={night}", token=token)
    return next(t for t in answer["tasks"] if t["project"] == ORION)


def sky(task_, indices):
    return [collab.Region.read(task_["cells"][i]) for i in indices]


def overlap(a, b):
    """Sky two lists have in common, counting only cells that really coincide.

    Neighbouring cells of one tiling overlap by a tenth of a frame on purpose,
    so a panel next to somebody else's always shares a sliver with it. That
    is not two rigs on the same panel; half a cell or more is.
    """
    return sum(collab.overlap_area(x, y) for x in a for y in b
               if collab.overlap_area(x, y) > 0.5 * min(x.area(), y.area()))


# Alone on twelve panels with six hours, a rig is not given all twelve: a
# visit has to be worth stacking on its own - ten frames at least, in each
# filter - and six hours holds six such visits, not twelve.
wide1 = held(WIDE_TOKEN, "2026-10-01")
frames1 = wide1["visit"]["frames"]
case("...and, alone on it, is given as many panels as its night holds",
     1 < len(wide1["share"]) < len(wide1["cells"]),
     f'{len(wide1["share"])} of {len(wide1["cells"])} for 6 hours')
case("...with enough frames on each of them to stack",
     all(n >= 10 for n in frames1.values()), str(frames1))
case("...and the night about full",
     0.8 < len(wide1["share"]) * (wide1["visit"]["seconds"] + 90) / (6 * 3600) <= 1.0,
     f'{len(wide1["share"])} x {wide1["visit"]["seconds"]:.0f}s of 6 h')
first_version = wide1["version"]

# A second rig, narrower camera, half the hours, arriving the same night.
status, second = call("POST", f"/api/v1/agent/projects/{ORION}/join", {},
                      token=NARROW_TOKEN)
case("a second rig can join the same project", status == 200)
case("...with more cells, because its camera is narrower",
     len(second["task"]["cells"]) > len(task["cells"]),
     f'{len(second["task"]["cells"])} vs {len(task["cells"])}')
narrow1 = held(NARROW_TOKEN, "2026-10-01")
case("...and takes a night's worth of its own, not all of it",
     0 < len(narrow1["share"]) < len(narrow1["cells"]),
     f'{len(narrow1["share"])} of {len(narrow1["cells"])}')

# The first rig's list holds. Somebody joining at nine must not move the
# panels a rig has been shooting since eight.
same = held(WIDE_TOKEN, "2026-10-01")
case("the first rig's list for the night holds when a second rig joins",
     same["share"] == wide1["share"] and same["version"] == first_version,
     f'{len(same["share"])} panels, version {same["version"]}')
narrow_sky = sky(narrow1, narrow1["share"])
case("...and the newcomer is sent to sky the first rig is not on tonight",
     overlap(sky(wide1, wide1["share"]), narrow_sky)
     < 0.1 * sum(r.area() for r in narrow_sky),
     f'{overlap(sky(wide1, wide1["share"]), narrow_sky):.1f} square degrees in common')

# ------------------------------------------------- a camera that has turned
print("\n-- a camera that turns out to sit somewhere else --")

# The wide rig has no rotator and said its camera sits at 0. The first plate
# solve of the night finds it at 268: its program lays its mosaic again and
# says so in its profile, and the server cuts its cells again to match - the
# grid transposes - and deals tonight's list afresh on them.
before = held(WIDE_TOKEN, "2026-10-01")
across_before = max(c["column"] for c in before["cells"]) + 1
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "wide", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 6.0, "rotation": 268.0,
                "scale": 206.265 * 3.76 / 389.0,
                "field": [206.265 * 3.76 / 389.0 * 9576 / 3600.0,
                          206.265 * 3.76 / 389.0 * 6388 / 3600.0]}},
    token=WIDE_TOKEN)
after = held(WIDE_TOKEN, "2026-10-01")
across_after = max(c["column"] for c in after["cells"]) + 1
case("a camera that reports a new angle has its cells cut again",
     (len(after["cells"]) != len(before["cells"])
      or all(abs(c["rotation"] - 268.0) < 1e-6 for c in after["cells"]))
     and after["version"] > before["version"],
     f"{len(before['cells'])} cells -> {len(after['cells'])} at 268, version "
     f"{before['version']} -> {after['version']}")
case("...laid along the camera's own axes, cell for panel with the rig's program",
     all(abs(c["rotation"] - 268.0) < 1e-6 for c in after["cells"])
     and len(after["cells"]) == len(collab.camera_grid(
         collab.Region.read(after["region"]), 206.265 * 3.76 / 389.0 * 9576 / 3600.0,
         206.265 * 3.76 / 389.0 * 6388 / 3600.0, 268.0)),
     f"{len(after['cells'])} cells")
case("...and tonight's list dealt afresh on the new cells",
     after["share"] and all(0 <= i < len(after["cells"]) for i in after["share"]),
     f'{len(after["share"])} of {len(after["cells"])}')
again = held(WIDE_TOKEN, "2026-10-01")
case("...and holds from then on", again["version"] == after["version"])
# The hours a rig gives come off its plan and can shrink - a window pinned
# shorter, a target that sets early. The list dealt for tonight holds all
# the same: a sequence drawn up at dusk is the sequence that runs, and the
# panels the shorter night does not reach are simply not shot. The smaller
# hours are dealt for the next night.
before = held(WIDE_TOKEN, "2026-10-01")
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "wide", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 2.0, "rotation": 268.0,
                "scale": 206.265 * 3.76 / 389.0,
                "field": [206.265 * 3.76 / 389.0 * 9576 / 3600.0,
                          206.265 * 3.76 / 389.0 * 6388 / 3600.0]}},
    token=WIDE_TOKEN)
same_night = held(WIDE_TOKEN, "2026-10-01")
case("a rig whose night shrank keeps tonight's list exactly as dealt",
     same_night["share"] == before["share"] and same_night["version"] == before["version"],
     f'{len(before["share"])} panels, version {before["version"]} -> {same_night["version"]}')
shorter = held(WIDE_TOKEN, "2026-10-01b")
case("...and the next night is dealt for the two hours it now gives",
     0 < len(shorter["share"]) < len(before["share"])
     and shorter["version"] > before["version"],
     f'{len(before["share"])} -> {len(shorter["share"])} panels for 2 h')
case("...fitting the two hours",
     len(shorter["share"]) * (shorter["visit"]["seconds"] + 90) <= 2 * 3600 + 1,
     f'{len(shorter["share"])} x {shorter["visit"]["seconds"]:.0f}s')

# Back to straight for the nights below, which were written for it.
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "wide", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 6.0,
                "scale": 206.265 * 3.76 / 389.0,
                "field": [206.265 * 3.76 / 389.0 * 9576 / 3600.0,
                          206.265 * 3.76 / 389.0 * 6388 / 3600.0]}},
    token=WIDE_TOKEN)
held(WIDE_TOKEN, "2026-10-01")

# ============================================================== night by night
print("\n-- three rigs, four nights --")

# The pressure test: a field too big for anyone to finish in a night, three
# telescopes on it, and the server dealing each night from what came in the
# night before. What has to come out: no two rigs on the same panel the same
# night; every rig, over the nights, on every part of the field, rather than
# one camera owning one corner; panels that are done left alone; and each
# rig's list holding still until its night turns.
_, third_made = call("POST", "/api/v1/agents", {"name": "third"}, token=ADMIN)
THIRD_TOKEN = third_made["token"]
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "third", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 4.0,
                "scale": 206.265 * 3.76 / 389.0,
                "field": [206.265 * 3.76 / 389.0 * 9576 / 3600.0,
                          206.265 * 3.76 / 389.0 * 6388 / 3600.0]}},
    token=THIRD_TOKEN)
call("POST", f"/api/v1/agent/projects/{ORION}/join", {}, token=THIRD_TOKEN)

RIGS = [("wide", WIDE_TOKEN), ("narrow", NARROW_TOKEN), ("third", THIRD_TOKEN)]
visited = {name: {} for name, _ in RIGS}          # name -> cell index -> nights
tasks_by = {}


def shoot(name, token, night):
    """A rig does its list for the night and reports every panel of it."""
    task_ = held(token, night)
    tasks_by[name] = task_
    rows = []
    for index in task_["share"]:
        visited[name].setdefault(index, []).append(night)
        cell = task_["cells"][index]
        for filter_name, count in task_["visit"]["frames"].items():
            exposure = next(f["exposure"] for f in task_["filters"]
                            if f["filter"] == filter_name)
            rows.append({
                "task": task_["id"], "night": night, "panel": str(index),
                "filterName": filter_name, "frames": count,
                "seconds": count * exposure, "exposure": exposure,
                "footprint": {"ra": cell["ra"], "dec": cell["dec"],
                              "width": cell["width"], "height": cell["height"]},
                "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0})
    _, answer = call("POST", "/api/v1/agent/report", {"contributions": rows},
                     token=token)
    return task_, all(r["accepted"] for r in answer["recorded"])


NIGHTS = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
clashes = []
accepted = True
per_night = {}
for night in NIGHTS:
    lists = {}
    for name, token in RIGS:
        task_, ok = shoot(name, token, night)
        accepted = accepted and ok
        lists[name] = sky(task_, task_["share"])
    per_night[night] = lists
    for i, (a, _) in enumerate(RIGS):
        for b, _ in RIGS[i + 1:]:
            common = overlap(lists[a], lists[b])
            if common > 0:
                clashes.append((night, a, b, round(common, 1)))

case("every night's reports are accepted, panel by panel", accepted)
case("no two rigs are sent to the same sky on the same night",
     not clashes, str(clashes) if clashes else "no clashes over four nights")

# Evenness: over the nights, each rig is sent everywhere - as far as its
# nights reach. The narrow camera has a hundred and fifty cells and does
# three a night; four nights cannot cover that, and what matters for it is
# that it is never sent back to one before it has been to the rest.
for name, _ in RIGS:
    cells_ = len(tasks_by[name]["cells"])
    visits = sum(len(nights) for nights in visited[name].values())
    seen = len(visited[name])
    case(f"{name} has, over four nights, been sent across the field",
         seen >= 0.8 * min(cells_, visits),
         f"{seen} of its {cells_} cells in {visits} visits")
    twice = max(len(nights) for nights in visited[name].values())
    least = min((len(visited[name].get(i, [])) for i in range(cells_)),
                default=0)
    # The rig dealt first each night gets exactly this; the ones after it
    # take what the first has left, and with ten of twelve panels spoken for
    # a night can be a visit behind.
    slack = 1 if name == "wide" else 2
    case(f"...and never to one panel twice before the rest once",
         twice - least <= slack, f"most {twice}, least {least}")

# The depth map drives it: what each rig is sent to on night two is what it
# was not sent to on night one.
first, second_ = per_night[NIGHTS[0]]["wide"], per_night[NIGHTS[1]]["wide"]
case("the wide rig's second night is the sky its first night was not",
     overlap(first, second_) == 0,
     f"{overlap(first, second_):.1f} square degrees repeated")

# And a list holds within a night, however much comes in from the others.
before = held(WIDE_TOKEN, NIGHTS[-1])
after = held(WIDE_TOKEN, NIGHTS[-1])
case("a rig's list for the night does not move while it is shooting it",
     before["share"] == after["share"] and before["version"] == after["version"])

# Panels that are finished are left alone. Push one cell of the wide rig's
# tiling past the project's ten hours and it drops out of the next night.
_, wide_now = call("GET", f"/api/v1/agent/task?night={NIGHTS[-1]}", token=WIDE_TOKEN)
wide_task = next(t for t in wide_now["tasks"] if t["project"] == ORION)
done_cell = wide_task["cells"][0]
call("POST", "/api/v1/agent/report", {"contributions": [{
    "task": wide_task["id"], "night": "2026-10-05", "panel": "0",
    "filterName": "Ha", "frames": 200, "seconds": 60000, "exposure": 300,
    "footprint": {"ra": done_cell["ra"], "dec": done_cell["dec"],
                  "width": done_cell["width"], "height": done_cell["height"]},
    "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0}]},
    token=WIDE_TOKEN)
later = [held(token, "2026-10-06") for _, token in RIGS]
done_region = collab.Region.read(done_cell)
touched = sum(overlap(sky(t, t["share"]), [done_region]) for t in later)
case("a panel at the project's depth is not handed to anybody",
     touched == 0, f"{touched:.2f} of {done_region.area():.1f} square degrees")

# The busier rig still does the most: six hours of panels against three.
case("the rig giving the most hours is sent to the most panels a night",
     len(tasks_by["wide"]["share"]) > len(tasks_by["narrow"]["share"])
     * tasks_by["narrow"]["cells"][0]["width"] * tasks_by["narrow"]["cells"][0]["height"]
     / (tasks_by["wide"]["cells"][0]["width"] * tasks_by["wide"]["cells"][0]["height"]),
     f'wide {len(tasks_by["wide"]["share"])} big panels, '
     f'narrow {len(tasks_by["narrow"]["share"])} small ones')

# A rig with no rotator reports the angle its camera sits at, and the server
# cuts its cells to what that camera really spans: at 268 degrees the wide
# rig's long axis runs north-south, so it gets more cells across than down.
_, fixed_made = call("POST", "/api/v1/agents", {"name": "fixed"}, token=ADMIN)
scale = 206.265 * 3.76 / 389.0
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "fixed", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 4.0, "rotation": 268.0,
                "scale": scale, "field": [scale * 9576 / 3600.0,
                                          scale * 6388 / 3600.0]}},
    token=fixed_made["token"])
_, turned = call("POST", f"/api/v1/agent/projects/{ORION}/join", {},
                 token=fixed_made["token"])
cells = turned["task"]["cells"]
across = max(c["column"] for c in cells) + 1
down = max(c["row"] for c in cells) + 1
# ------------------------------------------------- one filter a night each
print("\n-- a three-filter mosaic: one filter a night per telescope --")

# A mosaic night is one filter per telescope, and which filter is the
# collaboration's choice: the one with the most still outstanding once what
# the other rigs are already putting in tonight is counted. Three identical
# rigs on a project wanting equal Ha, OIII and SII are sent one to each; a
# filter at depth is handed to nobody; a rig alone on it rotates night by night.
def enrol_nb(name, hours=6.0):
    _, made = call("POST", "/api/v1/agents", {"name": name}, token=ADMIN)
    scale_ = 206.265 * 3.76 / 389.0
    call("POST", "/api/v1/agent/hello", {
        "protocol": collab.PROTOCOL,
        "profile": {"name": name, "focalLength": 389.0, "pixelSize": 3.76,
                    "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                    "filters": {"Ha": 3.0, "OIII": 3.0, "SII": 3.0},
                    "exposures": {"Ha": 300.0, "OIII": 300.0, "SII": 300.0},
                    "hoursPerNight": hours, "scale": scale_,
                    "field": [scale_ * 9576 / 3600.0, scale_ * 6388 / 3600.0]}},
        token=made["token"])
    return made["token"]


NB = [("nb1", enrol_nb("nb1")), ("nb2", enrol_nb("nb2")), ("nb3", enrol_nb("nb3"))]
_, band = call("POST", "/api/v1/projects", {
    "name": "Three-band", "region": {"ra": 300.0, "dec": 40.0, "width": 16.0, "height": 8.0},
    "requirements": {"filters": {"Ha": 3.0, "OIII": 3.0, "SII": 3.0}},
    "goals": {"Ha": 10.0, "OIII": 10.0, "SII": 10.0}}, token=ADMIN)
BAND = band["project"]["id"]
for _, tok in NB:
    status, _ = call("POST", f"/api/v1/agent/projects/{BAND}/join", {}, token=tok)
    assert status == 200, status


def held_on(token, night, project_id, extra=""):
    _, answer = call("GET", f"/api/v1/agent/task?night={night}{extra}", token=token)
    return next(t for t in answer["tasks"] if t["project"] == project_id)


def report_night(token, task_, night):
    rows = []
    for index in task_["share"]:
        cell = task_["cells"][index]
        for filter_name, count in task_["visit"]["frames"].items():
            exposure = next(f["exposure"] for f in task_["filters"] if f["filter"] == filter_name)
            rows.append({"task": task_["id"], "night": night, "panel": str(index),
                         "filterName": filter_name, "frames": count,
                         "seconds": count * exposure, "exposure": exposure,
                         "footprint": {"ra": cell["ra"], "dec": cell["dec"],
                                       "width": cell["width"], "height": cell["height"]},
                         "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0})
    call("POST", "/api/v1/agent/report", {"contributions": rows}, token=token)


night1 = {name: held_on(tok, "2026-11-01", BAND) for name, tok in NB}
filters_night1 = {name: list(t["visit"]["frames"]) for name, t in night1.items()}
case("each telescope is given exactly one filter for the night",
     all(len(f) == 1 for f in filters_night1.values()), str(filters_night1))
case("...and the visit names it",
     all(t["visit"].get("filter") == list(t["visit"]["frames"])[0] for t in night1.values()))
case("three telescopes on three equal filters are sent one to each",
     len({f[0] for f in filters_night1.values()}) == 3, str(filters_night1))
case("...with the whole night's frames in that filter",
     all(list(t["visit"]["frames"].values())[0] >= 10 for t in night1.values()),
     str({n: t["visit"]["frames"] for n, t in night1.items()}))

for name, tok in NB:
    report_night(tok, night1[name], "2026-11-01")

# Push OIII to the project's depth everywhere: nobody is sent to it after that.
o_task = night1["nb2"]
done_rows = []
for index, cell in enumerate(o_task["cells"]):
    done_rows.append({"task": o_task["id"], "night": "2026-11-02", "panel": str(index),
                      "filterName": "OIII", "frames": 200, "seconds": 60000, "exposure": 300,
                      "footprint": {"ra": cell["ra"], "dec": cell["dec"],
                                    "width": cell["width"], "height": cell["height"]},
                      "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0})
call("POST", "/api/v1/agent/report", {"contributions": done_rows}, token=NB[1][1])
night3 = {name: held_on(tok, "2026-11-03", BAND) for name, tok in NB}
filters_night3 = {name: list(t["visit"]["frames"])[0] for name, t in night3.items()}
case("a filter at the project's depth is handed to nobody",
     "O" not in filters_night3.values(), str(filters_night3))
case("...and the telescopes are spread over what is left",
     set(filters_night3.values()) == {"H", "S"}, str(filters_night3))

# A rig alone on a three-filter mosaic: one filter a night, moving on as
# each becomes the deepest.
_, lone = call("POST", "/api/v1/projects", {
    "name": "Lone three-band", "region": {"ra": 20.0, "dec": 60.0, "width": 10.0, "height": 6.0},
    "requirements": {"filters": {"Ha": 3.0, "OIII": 3.0, "SII": 3.0}},
    "goals": {"Ha": 4.0, "OIII": 4.0, "SII": 4.0}}, token=ADMIN)
LONE = lone["project"]["id"]
call("POST", f"/api/v1/agent/projects/{LONE}/join", {}, token=NB[0][1])
lone_filters = []
for night in ("2026-11-10", "2026-11-11", "2026-11-12"):
    t = held_on(NB[0][1], night, LONE)
    lone_filters.append(list(t["visit"]["frames"])[0])
    report_night(NB[0][1], t, night)
case("a telescope alone on a three-filter mosaic shoots one filter a night",
     all(len(held_on(NB[0][1], n, LONE)["visit"]["frames"]) == 1
         for n in ("2026-11-10", "2026-11-11", "2026-11-12")))
case("...and moves to the thinnest filter each night rather than repeating one",
     len(set(lone_filters)) == 3, " -> ".join(lone_filters))

# The Moon decides the kind of filter. A rig tells the server how lit the
# Moon is and how much of its dark hours it is up; a bright Moon makes it a
# night for Ha or SII, a dark one a night for OIII, LRGB - what cannot be
# shot any other time - with the narrowband kept for the moonlit nights.
_, moony = call("POST", "/api/v1/projects", {
    "name": "Moon test", "region": {"ra": 40.0, "dec": 50.0, "width": 10.0, "height": 6.0},
    "requirements": {"filters": {"Ha": 3.0, "OIII": 3.0, "SII": 3.0}},
    "goals": {"Ha": 6.0, "OIII": 6.0, "SII": 6.0}}, token=ADMIN)
MOONY = moony["project"]["id"]
call("POST", f"/api/v1/agent/projects/{MOONY}/join", {}, token=NB[2][1])
dark_night = held_on(NB[2][1], "2026-11-15", MOONY, "&moon=0.05&moonUp=0.1")
case("under no Moon the night goes to what moonlight would spoil: OIII first",
     dark_night["visit"].get("filter") == "O", str(dark_night["visit"]))
bright_night = held_on(NB[2][1], "2026-11-26", MOONY, "&moon=0.95&moonUp=0.9")
case("under a bright Moon the night goes to the narrowband that shoots through it",
     bright_night["visit"].get("filter") in ("H", "S"), str(bright_night["visit"]))
# All of the Moon-proof work done: a bright night is still spent on what is left.
for name in ("Ha", "SII"):
    rows_ = [{"task": bright_night["id"], "night": "2026-11-27", "panel": str(i),
              "filterName": name, "frames": 200, "seconds": 60000, "exposure": 300,
              "footprint": {"ra": c["ra"], "dec": c["dec"], "width": c["width"], "height": c["height"]},
              "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0}
             for i, c in enumerate(bright_night["cells"])]
    call("POST", "/api/v1/agent/report", {"contributions": rows_}, token=NB[2][1])
leftover = held_on(NB[2][1], "2026-11-28", MOONY, "&moon=0.95&moonUp=0.9")
case("...and when the narrowband is finished, a bright night still shoots what is left",
     leftover["visit"].get("filter") == "O", str(leftover["visit"]))

# Nothing moves a list under its night - not even a list that looks wrong.
# Whatever the server would deal differently now, it deals the next night.
from server import app as server_app                                 # noqa: E402
old_style = held_on(NB[0][1], "2026-12-01", BAND)
stale = dict(old_style)
stale["visit"] = {"seconds": 18000.0,
                  "frames": {f["filter"]: 10 for f in old_style["filters"]}}
server_app.store.set_task(stale)
again = held_on(NB[0][1], "2026-12-01", BAND)
case("a list dealt for tonight holds for tonight, whatever it looks like",
     again["version"] == stale["version"] and len(again["visit"]["frames"]) > 1,
     f'v{again["version"]}, {again["visit"]["frames"]}')
tomorrow = held_on(NB[0][1], "2026-12-02", BAND)
case("...and is dealt properly, one filter, when the night turns",
     len(tomorrow["visit"]["frames"]) == 1 and tomorrow["version"] > again["version"],
     f'{tomorrow["visit"]["frames"]} v{tomorrow["version"]}')
status, health = call("GET", "/api/v1/health")
case("the health line says which build is running",
     bool(health.get("version")), str(health.get("version")))

# A list made before the rig said anything about its Moon holds for its
# night like any other; the Moon is counted the next night. (The join now
# carries the Moon, so a blind deal only happens on an old program.)
_, blind_project = call("POST", "/api/v1/projects", {
    "name": "Blind deal", "region": {"ra": 60.0, "dec": 30.0, "width": 10.0, "height": 6.0},
    # OIII first in the project's order, so a deal that knows nothing of the
    # Moon - equal progress, tie to the first - lands on it.
    "requirements": {"filters": {"OIII": 3.0, "Ha": 3.0}},
    "goals": {"OIII": 6.0, "Ha": 6.0}}, token=ADMIN)
BLIND = blind_project["project"]["id"]
call("POST", f"/api/v1/agent/projects/{BLIND}/join", {}, token=NB[1][1])
blind = held_on(NB[1][1], "2026-12-05", BLIND)
case("dealt with no word of the Moon, the night goes to what moonlight would spoil",
     blind["visit"].get("filter") == "O" and "moon" not in blind["visit"], str(blind["visit"]))
told = held_on(NB[1][1], "2026-12-05", BLIND, "&moon=0.9&moonUp=0.9")
case("...and holds for the night even when the Moon is reported later",
     told["visit"].get("filter") == "O" and told["version"] == blind["version"], str(told["visit"]))
next_night = held_on(NB[1][1], "2026-12-06", BLIND, "&moon=0.9&moonUp=0.9")
case("...the next night is dealt with the Moon in mind",
     next_night["visit"].get("filter") == "H" and next_night["version"] > blind["version"],
     str(next_night["visit"]))

# Joining twice is one share. The second press hands back the task the rig
# already holds, and a rig that somehow holds two is left with the newest.
_, first_join = call("POST", f"/api/v1/agent/projects/{BLIND}/join", {}, token=NB[2][1])
_, second_join = call("POST", f"/api/v1/agent/projects/{BLIND}/join", {}, token=NB[2][1])
case("joining a project twice hands back the share already held",
     second_join.get("alreadyJoined") is True
     and second_join["task"]["id"] == first_join["task"]["id"])
dupe = dict(first_join["task"])
dupe.update({"id": "dupe-task", "issued": float(dupe.get("issued") or 0.0) - 100.0})
server_app.store.add_task(dupe)
_, listing = call("GET", "/api/v1/agent/task?night=2026-12-06", token=NB[2][1])
live = [t for t in listing["tasks"] if t["project"] == BLIND and t["state"] in ("offered", "accepted")]
retired = server_app.store.task("dupe-task")
case("a rig holding two shares of one project is left with the newest",
     len(live) == 1 and live[0]["id"] == first_join["task"]["id"]
     and retired["state"] == "superseded",
     f'{len(live)} live, dupe is {retired["state"]}')

# ------------------------------------------------------- one target, one spot
print("\n-- a single-target collaboration --")

# One object, and everybody points at it. The narrow camera would tile the
# framer's 5.3 by 3.5 degree field into a dozen cells as a mosaic; as a single
# target it gets one cell, its own frame, centred on the object.
_, spot = call("POST", "/api/v1/projects", {
    "name": "Horsehead", "kind": "single",
    "region": {"ra": 85.25, "dec": -2.46, "width": 5.3, "height": 3.5,
               "rotation": 12.0},
    "requirements": {"filters": {"Ha": 3.0}}, "goals": {"Ha": 8.0}},
    token=ADMIN)
SPOT = spot["project"]["id"]
case("a project can be started as one target",
     spot["project"]["payload"].get("kind") == "single")
_, browse = call("GET", "/api/v1/agent/projects", token=NARROW_TOKEN)
case("...and says so when browsed",
     next(p for p in browse["projects"] if p["id"] == SPOT)["kind"] == "single")

status, one = call("POST", f"/api/v1/agent/projects/{SPOT}/join", {},
                   token=NARROW_TOKEN)
narrow_field = 206.265 * 3.76 / 1000.0 * 6000 / 3600.0
case("a narrow camera joining it is not sent to mosaic the framer's field",
     status == 200 and len(one["task"]["cells"]) == 1,
     f'{len(one["task"]["cells"])} cell(s)')
case("...its one cell is its own frame, centred on the object",
     abs(one["task"]["cells"][0]["width"] - narrow_field) < 1e-6
     and abs(one["task"]["cells"][0]["ra"] - 85.25) < 1e-9,
     f'{one["task"]["cells"][0]["width"]:.3f} degrees wide at '
     f'{one["task"]["cells"][0]["ra"]:.2f}')
case("...and the task says it is a single target",
     one["task"].get("kind") == "single")
tonight_one = call("GET", "/api/v1/agent/task?night=2026-10-08",
                   token=NARROW_TOKEN)[1]
spot_task = next(t for t in tonight_one["tasks"] if t["project"] == SPOT)
case("...dealt that one cell for the night, with the whole night on it",
     spot_task["share"] == [0]
     and spot_task["visit"]["frames"].get("H", 0) * 300 >= 3 * 3600 * 0.9,
     f'share {spot_task["share"]}, {spot_task["visit"]["frames"]} frames')

# A single target's depth map shows the union of everybody's fields of view
# as they were really turned. A 2 by 0.5 degree camera at a position angle
# of 90 covers half a degree east-west and two degrees north-south; credited
# north-up it put its seconds beside the field instead of above and below.
# The map also reaches beyond the region to take the frames around it in,
# says which cells are inside the box, and hands back the frames themselves.
call("POST", "/api/v1/agent/report", {"contributions": [{
    "task": one["task"]["id"], "night": "2026-10-08", "panel": "0", "filterName": "Ha",
    "frames": 12, "seconds": 3600, "exposure": 300,
    "footprint": {"ra": 85.25, "dec": -2.46, "width": 2.0, "height": 0.5, "rotation": 90.0},
    "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0}]}, token=NARROW_TOKEN)
_, spot_map = call("GET", f"/api/v1/agent/projects/{SPOT}/depth", token=NARROW_TOKEN)


def cell_near(grid_, dx, dy):
    """The cell whose centre is nearest `dx` degrees east and `dy` north of the region's centre."""
    import math as _m
    cosine = _m.cos(_m.radians(grid_["region"]["dec"]))
    best, best_d = None, None
    for i, c in enumerate(grid_["cells"]):
        x = (((c["ra"] - grid_["region"]["ra"] + 180.0) % 360.0) - 180.0) * cosine
        y = c["dec"] - grid_["region"]["dec"]
        d = (x - dx) ** 2 + (y - dy) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


north = spot_map["seconds"]["H"][cell_near(spot_map, 0.0, 0.8)]
east = spot_map["seconds"]["H"][cell_near(spot_map, 0.8, 0.0)]
case("a frame turned ninety degrees credits the cells above and below the field, not beside it",
     north > 0 and east == 0, f"north {north:.0f}s, east {east:.0f}s")
case("...the single target's map is a fine grid that says which cells are inside the region",
     spot_map["kind"] == "single" and len(spot_map["cells"]) > 1
     and any(c["inside"] for c in spot_map["cells"]) and spot_map["columns"] * spot_map["rows"] == len(spot_map["cells"]),
     f'{len(spot_map["cells"])} cells')
case("...and hands back each camera's field as it was shot, with its angle and its hours",
     len(spot_map["footprints"]) == 1 and spot_map["footprints"][0]["rotation"] == 90.0
     and spot_map["footprints"][0]["seconds"] == 3600 and spot_map["footprints"][0]["filter"] == "H",
     str(spot_map["footprints"])[:160])

# --------------------------------------------- changing one after it started
print("\n-- changing a project after it started --")

status, changed = call("PUT", f"/api/v1/projects/{ORION}", {
    "requirements": {"filters": {"Ha": 7.0}, "maxHfr": 4.0},
    "goals": {"Ha": 25.0}, "notes": "loosened: 7 nm is fine after all"},
    token=ADMIN)
case("the coordinator can change what a project asks for", status == 200)
case("...and only what was sent moves, filters in the one spelling",
     changed["project"]["payload"]["requirements"]["filters"] == {"H": 7.0}
     and changed["project"]["payload"]["goals"] == {"H": 25.0}
     and abs(changed["project"]["payload"]["region"]["width"] - 16.0) < 1e-6,
     "region kept, filters and goals replaced")

# Every rig on it finds out when it asks: the requirements travel with the task.
_, refreshed = call("GET", "/api/v1/agent/task", token=WIDE_TOKEN)
case("a rig on the project sees the new rules on its next poll",
     refreshed["requirementsByProject"][ORION]["maxHfr"] == 4.0,
     str(refreshed["requirementsByProject"][ORION]))

status, _ = call("PUT", f"/api/v1/projects/{ORION}", {"notes": "nope"},
                 token=WIDE_TOKEN)
case("a telescope's token cannot change a project", status == 401)

# A project the admin token started is nobody's but an owner's. Alice, owner
# by Discord id, may change it; a plain member may not. Bob signed out above,
# so a fresh member signs in for this.
status, _ = call("PUT", f"/api/v1/projects/{ORION}", {"notes": "owner by id"},
                 token=ALICE)
case("an owner by Discord id can change a project the admin token started",
     status == 200)
_, _, _, polled = sign_in("code-bob")
status, _ = call("PUT", f"/api/v1/projects/{ORION}", {"notes": "no"},
                 token=polled["token"])
case("...and a plain member cannot", status == 403)

status, closed = call("PUT", f"/api/v1/projects/{ORION}", {"status": "closed"},
                      token=ADMIN)
case("closing takes it off the open list",
     status == 200 and closed["project"]["status"] == "closed")
_, listing = call("GET", "/api/v1/agent/projects", token=WIDE_TOKEN)
case("...so a rig browsing no longer sees it",
     all(p["id"] != ORION for p in listing["projects"]))
case("...while what was collected is still on record",
     call("GET", f"/api/v1/projects/{ORION}")[1]["project"]["status"] == "closed")
call("PUT", f"/api/v1/projects/{ORION}", {"status": "open"}, token=ADMIN)

# A camera that cannot turn is tiled the way its program tiles it: along
# the camera's own axes, the long axis running north-south at 268, so the
# grid climbs the region in more rows than the same camera straight needs.
straight_across = max(c["column"] for c in task["cells"]) + 1
straight_down = max(c["row"] for c in task["cells"]) + 1
case("a camera that cannot turn is tiled the way it really sits",
     all(abs(c["rotation"] - 268.0) < 1e-6 for c in cells) and down > straight_down,
     f"turned {across}x{down}, straight {straight_across}x{straight_down}")
# And in the program's own order, so that cell i on the server is panel
# i + 1 on the rig and a share of neighbouring cells is a walk between
# neighbouring panels.
from astrocontrol import framing as _framing                       # noqa: E402
laid = _framing.mosaic_panels(
    turned["task"]["region"]["ra"], turned["task"]["region"]["dec"],
    scale * 9576 / 3600.0, scale * 6388 / 3600.0, rows=down, columns=across,
    overlap=0.1, position_angle=268.0, align="fixed")
case("...cell for panel, in the order the program walks them",
     len(laid) == len(cells) and all(
         abs(c["ra"] / 15.0 - p["ra"]) < 1e-4 and abs(c["dec"] - p["dec"]) < 1e-4
         for c, p in zip(cells, laid)),
     f"{len(cells)} cells, {len(laid)} panels")

# A task tiled by the old rule - north-up cells of the turned frame's
# bounding box - is cut again on the rig's next poll, without the camera
# having moved, so a project started before the change is not stuck with
# a grid that no longer matches the rig's panels.
from server import app as _server_app                                # noqa: E402
stale_task = dict(_server_app.store.task(turned["task"]["id"]))
old_cells = collab.grid(collab.Region.read(stale_task["region"]),
                        *collab.footprint(scale * 9576 / 3600.0, scale * 6388 / 3600.0, 268.0), 0.1)
stale_task["cells"] = old_cells
stale_task["share"] = [0, 1, 2]
_server_app.store.set_task(stale_task)
_, polled = call("GET", "/api/v1/agent/task?night=2026-12-10", token=fixed_made["token"])
recut = next(t for t in polled["tasks"] if t["id"] == turned["task"]["id"])


def same_centres(a, b):
    return len(a) == len(b) and all(
        abs(x["ra"] - y["ra"]) < 0.01 and abs(x["dec"] - y["dec"]) < 0.01 for x, y in zip(a, b))


# On this region both rules happen to make ten cells, so it is the centres
# that tell them apart: the old grid's are gone and the program's are back.
case("cells cut by the old rule are cut again on the next poll, camera unmoved",
     same_centres(recut["cells"], cells) and not same_centres(recut["cells"], old_cells)
     and all(0 <= i < len(recut["cells"]) for i in recut["share"]),
     f"{len(old_cells)} old cells -> {len(recut['cells'])}, share {recut['share']}")
_, polled_again = call("GET", "/api/v1/agent/task?night=2026-12-10", token=fixed_made["token"])
same_again = next(t for t in polled_again["tasks"] if t["id"] == turned["task"]["id"])
case("...and once they match, nothing is cut again",
     same_again["version"] == recut["version"] and same_again["cells"] == recut["cells"])

# The camera's angle in the profile moves by a few hundredths of a degree
# with every plate solve. That is the same tiling with a wobble, not a new
# rule, and must not re-cut the cells - re-cutting cleared the share and
# restarted the rig's run every couple of hours, all night.
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "fixed", "focalLength": 389.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"Ha": 3.0}, "hoursPerNight": 4.0, "rotation": 268.08,
                "scale": scale, "field": [scale * 9576 / 3600.0, scale * 6388 / 3600.0]}},
    token=fixed_made["token"])
_, wobbled = call("GET", "/api/v1/agent/task?night=2026-12-10", token=fixed_made["token"])
wobble_task = next(t for t in wobbled["tasks"] if t["id"] == turned["task"]["id"])
case("a solve's worth of wobble in the camera angle does not re-cut the cells",
     wobble_task["version"] == recut["version"] and wobble_task["cells"] == recut["cells"]
     and wobble_task["share"] == recut["share"],
     f'v{recut["version"]} -> v{wobble_task["version"]}')

# --------------------------------------- dealt at the rig's own exposures
# A share is dealt at the exposure each rig shoots that filter at - what its
# darks are built for - so every light it takes can be calibrated. A default
# outside what the project takes is refused at the door, naming the filter.
_, strict = call("POST", "/api/v1/projects", {
    "name": "Long subs only",
    "region": {"ra": 120.0, "dec": 20.0, "width": 1.0, "height": 1.0, "rotation": 0.0},
    "requirements": {"filters": {"Ha": 3.0}, "minExposure": 300, "maxExposure": 900},
    "goals": {"Ha": 4.0},
}, token=ADMIN)
STRICT = strict["project"]["id"]
LONG_TOKEN, _ = enrol("long-subs", 389.0, 9576, 6388, 4.0)
status, dealt = call("POST", f"/api/v1/agent/projects/{STRICT}/join",
                     {"exposures": {"ha": 600.0, "L": 120.0}}, token=LONG_TOKEN)
case("a share is dealt at the rig's own exposure for the filter",
     status == 200 and dealt["task"]["filters"][0]["exposure"] == 600.0,
     str(dealt.get("task", {}).get("filters") or dealt))
SHORT_TOKEN, _ = enrol("short-subs", 389.0, 9576, 6388, 4.0)
status, refused = call("POST", f"/api/v1/agent/projects/{STRICT}/join",
                       {"exposures": {"H": 120.0}}, token=SHORT_TOKEN, expect=409)
case("a default the project will not take is refused, naming the filter",
     status == 409 and "H at 120s" in str(refused) and "Equipment" in str(refused),
     str(refused))
NONE_TOKEN, _ = enrol("no-defaults", 389.0, 9576, 6388, 4.0)
status, middle = call("POST", f"/api/v1/agent/projects/{STRICT}/join", {},
                      token=NONE_TOKEN)
case("a rig that names no exposure still gets the middle of the range",
     status == 200 and middle["task"]["filters"][0]["exposure"] == 600.0,
     str(middle.get("task", {}).get("filters")))

# A rig takes part in the filters it carries. Three RASAs with one fixed
# filter each were turned away from a project wanting H, O, R, G and B -
# which wanted exactly what they had - and a rig that did get in was dealt
# every filter of the project, carried or not.
_, lrgb = call("POST", "/api/v1/projects", {
    "name": "Five filters", "kind": "single",
    "region": {"ra": 10.68, "dec": 41.27, "width": 3.0, "height": 2.0, "rotation": 0.0},
    "requirements": {"filters": {"H": 5.0, "O": 5.0, "R": None, "G": None, "B": None}},
    "goals": {"H": 10.0, "O": 10.0, "R": 10.0, "G": 10.0, "B": 10.0},
}, token=ADMIN)
FIVE = lrgb["project"]["id"]
_, rasa = call("POST", "/api/v1/agents", {"name": "rasa-r"}, token=ADMIN)
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "rasa-r", "focalLength": 620.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"R": None}, "hoursPerNight": 6.0,
                "scale": 206.265 * 3.76 / 620.0,
                "field": [206.265 * 3.76 / 620.0 * 9576 / 3600.0,
                          206.265 * 3.76 / 620.0 * 6388 / 3600.0]}},
    token=rasa["token"])
status, only_r = call("POST", f"/api/v1/agent/projects/{FIVE}/join",
                      {"exposures": {"R": 120.0}}, token=rasa["token"])
dealt_filters = [f["filter"] for f in (only_r.get("task") or {}).get("filters") or []]
case("a rig with one fixed filter joins a five-filter project",
     status == 200, str(only_r)[:120])
case("...and is dealt that filter alone, at its own exposure",
     dealt_filters == ["R"] and only_r["task"]["filters"][0]["exposure"] == 120.0,
     str((only_r.get("task") or {}).get("filters")))
_, r_night = call("GET", f"/api/v1/agent/task?night=2026-10-07&project={FIVE}",
                  token=rasa["token"])
r_task = next((t for t in (r_night.get("tasks") or [r_night.get("task") or {}])
               if (t or {}).get("project") == FIVE), r_night.get("task") or {})
r_frames = ((r_task or {}).get("visit") or {}).get("frames") or {}
case("...and tonight's deal for it names no other filter",
     set(r_frames) <= {"R"} and r_frames.get("R", 0) > 0, str(r_frames))

# A single field's night goes to the thinnest filter first. The M31 test
# collaboration sat at a third of the depth in blue for a week: every rig's
# night was split evenly by the goals, blue came last in the order, and dawn
# took it. Here H, O, R and G are eight hours deep of a ten-hour goal and B
# is empty, so a two-hour night is all blue, the next rig the same night is
# dealt around that, and a long night still puts blue first.
_, lopsided = call("POST", "/api/v1/projects", {
    "name": "Lopsided", "kind": "single",
    "region": {"ra": 10.68, "dec": 41.27, "width": 3.0, "height": 2.0, "rotation": 0.0},
    "requirements": {"filters": {"H": None, "O": None, "R": None, "G": None, "B": None}},
    "goals": {"H": 10.0, "O": 10.0, "R": 10.0, "G": 10.0, "B": 10.0},
}, token=ADMIN)
LOPSIDED = lopsided["project"]["id"]


def enrol_five(name, hours):
    _, made = call("POST", "/api/v1/agents", {"name": name}, token=ADMIN)
    scale_ = 206.265 * 3.76 / 620.0
    call("POST", "/api/v1/agent/hello", {
        "protocol": collab.PROTOCOL,
        "profile": {"name": name, "focalLength": 620.0, "pixelSize": 3.76,
                    "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                    "filters": {"H": None, "O": None, "R": None, "G": None, "B": None},
                    "exposures": {"H": 300.0, "O": 300.0, "R": 300.0, "G": 300.0, "B": 300.0},
                    "hoursPerNight": hours, "scale": scale_,
                    "field": [scale_ * 9576 / 3600.0, scale_ * 6388 / 3600.0]}},
        token=made["token"])
    return made["token"]


deep_token = enrol_five("deep-already", 2.0)
_, deep_join = call("POST", f"/api/v1/agent/projects/{LOPSIDED}/join", {}, token=deep_token)
deep_cell = deep_join["task"]["cells"][0]
_, _ = call("POST", "/api/v1/agent/report", {"contributions": [
    {"task": deep_join["task"]["id"], "night": "2026-10-01", "filterName": name,
     "frames": 96, "seconds": 8 * 3600, "exposure": 300,
     "footprint": {"ra": deep_cell["ra"], "dec": deep_cell["dec"],
                   "width": deep_cell["width"] * 3, "height": deep_cell["height"] * 3},
     "scale": 1.25, "hfr": 2.5, "guideRms": 0.7}
    for name in ("H", "O", "R", "G")]}, token=deep_token)
_, deep_night = call("GET", f"/api/v1/agent/task?night=2026-10-09&project={LOPSIDED}",
                     token=deep_token)
deep_task = next(t for t in deep_night["tasks"] if t["project"] == LOPSIDED)
deep_frames = deep_task["visit"]["frames"]
case("a short night on a lopsided field is spent entirely on the thin filter, and fits the night",
     list(deep_frames) == ["B"] and 20 <= deep_frames["B"] <= 24, str(deep_frames))
case("...and the task's filters are put in the night's order, the thin one first",
     [f["filter"] for f in deep_task["filters"]][0] == "B",
     str([f["filter"] for f in deep_task["filters"]]))

second_token = enrol_five("second-tonight", 2.0)
_, second_join = call("POST", f"/api/v1/agent/projects/{LOPSIDED}/join", {"night": "2026-10-09"},
                      token=second_token)
_, second_night = call("GET", f"/api/v1/agent/task?night=2026-10-09&project={LOPSIDED}",
                       token=second_token)
second_task = next(t for t in second_night["tasks"] if t["project"] == LOPSIDED)
case("the next rig dealt the same night is sent to blue as well, since it is still thinnest",
     list(second_task["visit"]["frames"]) == ["B"], str(second_task["visit"]["frames"]))

long_token = enrol_five("long-night", 9.0)
_, long_join = call("POST", f"/api/v1/agent/projects/{LOPSIDED}/join", {"night": "2026-10-09"},
                    token=long_token)
_, long_night = call("GET", f"/api/v1/agent/task?night=2026-10-09&project={LOPSIDED}",
                     token=long_token)
long_task = next(t for t in long_night["tasks"] if t["project"] == LOPSIDED)
long_frames = long_task["visit"]["frames"]
case("a long night still starts with blue and gives it the most, then levels the others up",
     list(long_frames)[0] == "B" and long_frames["B"] == max(long_frames.values())
     and len(long_frames) > 1 and all(n >= 10 for n in long_frames.values()),
     str(long_frames))

# Thinnest is measured against the goal. Asked for five hundred hours of H
# and a hundred of B, a field with twenty of H and four of B is further
# behind in H (4%) than in B (4%... just), and the night goes there.
_, steep = call("PUT", f"/api/v1/projects/{LOPSIDED}", {
    "goals": {"H": 500.0, "O": 10.0, "R": 10.0, "G": 10.0, "B": 10.0}}, token=ADMIN)
steep_token = enrol_five("steep-goals", 2.0)
_, steep_join = call("POST", f"/api/v1/agent/projects/{LOPSIDED}/join", {"night": "2026-10-10"},
                     token=steep_token)
_, steep_night = call("GET", f"/api/v1/agent/task?night=2026-10-10&project={LOPSIDED}",
                      token=steep_token)
steep_task = next(t for t in steep_night["tasks"] if t["project"] == LOPSIDED)
case("behind is measured against the goal: a filter asked for fifty times more comes first",
     list(steep_task["visit"]["frames"])[0] == "H", str(steep_task["visit"]["frames"]))
call("PUT", f"/api/v1/projects/{LOPSIDED}", {
    "goals": {"H": 10.0, "O": 10.0, "R": 10.0, "G": 10.0, "B": 10.0}}, token=ADMIN)

# A project that adds a filter deals it to the rigs already on it. Their
# filters were fixed the night they joined, and a project that added
# luminance a week later never dealt it to anyone until they left and
# joined again.
_, _ = call("PUT", f"/api/v1/projects/{LOPSIDED}", {
    "requirements": {"filters": {"H": None, "O": None, "R": None, "G": None, "B": None, "L": None}},
    "goals": {"H": 10.0, "O": 10.0, "R": 10.0, "G": 10.0, "B": 10.0, "L": 10.0}}, token=ADMIN)
lum_token = enrol_five("gets-luminance", 2.0)
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "gets-luminance", "focalLength": 620.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"H": None, "O": None, "R": None, "G": None, "B": None, "L": None},
                "exposures": {"H": 300.0, "O": 300.0, "R": 300.0, "G": 300.0, "B": 300.0, "L": 120.0},
                "hoursPerNight": 2.0, "scale": 206.265 * 3.76 / 620.0,
                "field": [206.265 * 3.76 / 620.0 * 9576 / 3600.0, 206.265 * 3.76 / 620.0 * 6388 / 3600.0]}},
    token=lum_token)
# The long-night rig carries L too, it just never said so; the one that
# does not carry it (the two-hour rig) must not be dealt it.
call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "long-night", "focalLength": 620.0, "pixelSize": 3.76,
                "sensorWidth": 9576, "sensorHeight": 6388, "binning": 1,
                "filters": {"H": None, "O": None, "R": None, "G": None, "B": None, "L": None},
                "hoursPerNight": 9.0, "scale": 206.265 * 3.76 / 620.0,
                "field": [206.265 * 3.76 / 620.0 * 9576 / 3600.0, 206.265 * 3.76 / 620.0 * 6388 / 3600.0]}},
    token=long_token)
_, lum_night = call("GET", f"/api/v1/agent/task?night=2026-10-11&project={LOPSIDED}", token=long_token)
long_now = next(t for t in lum_night["tasks"] if t["project"] == LOPSIDED)
# Blue and luminance are both at nothing of their goals here, so they share
# the night; the point is that luminance is in it at all.
case("a rig already on a project is dealt a filter the project added later, at the project's exposure",
     any(f["filter"] == "L" and f["exposure"] == 300.0 for f in long_now["filters"])
     and long_now["visit"]["frames"].get("L", 0) >= 10,
     f'{[f["filter"] for f in long_now["filters"]]} {long_now["visit"]["frames"]}')
_, no_lum = call("GET", f"/api/v1/agent/task?night=2026-10-11&project={LOPSIDED}", token=deep_token)
deep_now = next(t for t in no_lum["tasks"] if t["project"] == LOPSIDED)
case("...and a rig that does not carry the new filter is not dealt it",
     not any(f["filter"] == "L" for f in deep_now["filters"]),
     str([f["filter"] for f in deep_now["filters"]]))
_, lum_join = call("POST", f"/api/v1/agent/projects/{LOPSIDED}/join", {"exposures": {"L": 120.0}},
                   token=lum_token)
case("...and a rig joining now gets it at its own exposure",
     next((f["exposure"] for f in lum_join["task"]["filters"] if f["filter"] == "L"), None) == 120.0,
     str(lum_join["task"]["filters"]))

# The coordinator can make every rig's list for tonight be dealt afresh:
# the day the dealing rules changed, the lists already made for that night
# held, made the old way, and would have held all night.
status, _ = call("POST", f"/api/v1/projects/{LOPSIDED}/redeal", {}, token=deep_token)
case("a telescope cannot make a project deal everyone afresh", status == 401)
status, forgotten = call("POST", f"/api/v1/projects/{LOPSIDED}/redeal", {}, token=ADMIN)
case("the coordinator can, and every live list's night is forgotten",
     status == 200 and forgotten.get("forgotten") >= 3, str(forgotten))
_, again_night = call("GET", f"/api/v1/agent/task?night=2026-10-09&project={LOPSIDED}",
                      token=long_token)
again_task = next(t for t in again_night["tasks"] if t["project"] == LOPSIDED)
case("...and a rig's next ask is dealt again for its night",
     again_task.get("assignedNight") == "2026-10-09" and list(again_task["visit"]["frames"])[0] == "B",
     f'{again_task.get("assignedNight")} {again_task["visit"]["frames"]}')

# ------------------------------------------------- who is on the sky
# Every check-in may say where the telescope points and what it is doing;
# the group sees each other on the chart, and each project says how many
# telescopes are on it and how many are about tonight.
status, _ = call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL, "profile": PROFILE,
    "presence": {"ra": 5.5883, "dec": -5.39, "state": "imaging", "target": "M42"}},
    token=AGENT_TOKEN)
case("a check-in can carry where the telescope points", status == 200)
status, who = call("GET", "/api/v1/presence", token=LONG_TOKEN)
me = next((t for t in who.get("telescopes", []) if t["id"] == AGENT_ID), None)
case("everybody can see who is on the sky",
     status == 200 and me is not None and me["online"] is True
     and abs(me["ra"] - 5.5883) < 1e-6 and me["target"] == "M42" and me["state"] == "imaging",
     str(me))
case("...and how many telescopes are online", who.get("online", 0) >= 2, str(who.get("online")))

# ------------------------------------------------- who has contributed
# The opened card on the Collab tab: each person's accepted hours by filter,
# with their telescopes, grouped by the person rather than the telescope.
status, who_did = call("GET", f"/api/v1/agent/projects/{PROJECT}/people", token=AGENT_TOKEN)
case("a project lists who has contributed", status == 200 and len(who_did["people"]) >= 1,
     str(who_did)[:160])
top = who_did["people"][0] if who_did.get("people") else {}
case("...with accepted hours by filter and the telescope that shot them",
     top.get("hours", 0) > 0 and any(k.upper().startswith("O") for k in (top.get("byFilter") or {}))
     and len(top.get("telescopes") or []) >= 1 and "avatar" in top and "name" in top,
     f"{top.get('name')}: {top.get('hours')} h, {top.get('byFilter')}, {top.get('telescopes')}")
case("...and the total is the sum of the people",
     abs(who_did["hours"] - sum(p["hours"] for p in who_did["people"])) < 0.01)
status, _ = call("GET", "/api/v1/agent/projects/nonsense/people", token=AGENT_TOKEN, expect=404)
case("...and an unknown project is a 404", status == 404)

# The life behind the numbers: frames accepted and telescopes joining,
# newest first, across every open project, with who did it.
status, feed = call("GET", "/api/v1/agent/activity?hours=240", token=AGENT_TOKEN)
frames_events = [e for e in feed.get("events", []) if e["kind"] == "frames"]
joins = [e for e in feed.get("events", []) if e["kind"] == "joined"]
case("the activity feed lists the frames accepted lately, with who shot them and on what",
     status == 200 and frames_events and all("person" in e and "avatar" in e and e["filter"]
                                             and e["seconds"] > 0 and e["projectName"] for e in frames_events),
     str(frames_events[:1])[:200])
case("...and the telescopes that joined, newest first",
     joins and all(feed["events"][i]["at"] >= feed["events"][i + 1]["at"]
                   for i in range(len(feed["events"]) - 1)),
     f'{len(joins)} joins, {len(feed["events"])} events')
case("...with what the last day brought each project",
     PROJECT in feed.get("projects", {}) and "hoursToday" in feed["projects"][PROJECT]
     and "peopleToday" in feed["projects"][PROJECT], str(feed.get("projects", {}).get(PROJECT)))

# ------------------------------------------------- the sky, for a web page
# Open to anyone, with nothing in it but what a telescope said about
# itself: a page on the observatory's website shows where everybody points.
status, _ = call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL, "profile": PROFILE,
    "presence": {"ra": 5.6, "dec": -5.4, "state": "imaging", "target": "M42"}},
    token=AGENT_TOKEN)
request = urllib.request.Request(f"{BASE}/api/v1/sky?hours=14")
with urllib.request.urlopen(request, timeout=10) as response:
    sky = json.loads(response.read())
    cors = response.headers.get("Access-Control-Allow-Origin")
mine = next((t for t in sky["telescopes"] if t["name"] in (PROFILE.get("name"), "agent", "rig")
             or abs(t["ra"] - 5.6) < 1e-6), None)
case("the sky is public, and answers with permissive CORS",
     cors == "*" and mine is not None and mine["online"] is True, str(mine))
case("...and carries no ids, owners or pictures",
     mine is not None and not any(k in mine for k in ("id", "owner", "ownerName", "avatar", "project")),
     str(sorted(mine.keys())) if mine else "")
case("...and the track holds every pointing the telescope has sent tonight",
     mine is not None and len(mine["trail"]) >= 2
     and abs(mine["trail"][-1][1] - 5.6) < 1e-6 and abs(mine["trail"][-2][1] - 5.5883) < 1e-3,
     str((mine or {}).get("trail")))
for name, kind in (("widget.js", "javascript"), ("stars.json", "json"), ("index.html", "html")):
    with urllib.request.urlopen(f"{BASE}/sky/{name}", timeout=10) as response:
        body, served = response.read(), response.headers.get("Content-Type", "")
    if kind not in served or len(body) < 100:
        break
else:
    body = b"ok"
case("the widget, the stars and the page are served", body != b"" and kind == "html", served)
status, _ = call("GET", "/sky/..%2Fcollab.sqlite")
case("...and nothing else from that folder", status == 404)
# The owner's Discord picture and name travel with the telescope, for the
# chart. An agent enrolled by the owner's token has no signed-in person
# behind it, so its picture is blank rather than a broken link.
case("a telescope carries its owner's name and picture for the chart",
     "avatar" in me and me["avatar"] == "" and "ownerName" in me, str(me))
from server import app as _srv                                       # noqa: E402
case("...a signed-in person's picture is a Discord CDN link",
     _srv._avatar_url({"id": "1001", "avatar": "abc123"})
     == "https://cdn.discordapp.com/avatars/1001/abc123.png?size=64"
     and _srv._avatar_url({"id": "1001", "avatar": "a_moving"}).endswith("a_moving.gif?size=64")
     and _srv._avatar_url({"id": "1001", "avatar": ""}) == "")
status, listing = call("GET", "/api/v1/agent/projects", token=LONG_TOKEN)
strict_card = next((p for p in listing["projects"] if p["id"] == STRICT), {})
case("a project says how many telescopes are on it",
     strict_card.get("participants") == 2
     and strict_card.get("participantsOnline") == 2
     and set(strict_card.get("participantNames") or []) == {"long-subs", "no-defaults"},
     str({k: strict_card.get(k) for k in ("participants", "participantsOnline", "participantNames")}))
status, _ = call("POST", "/api/v1/agent/hello", {"protocol": collab.PROTOCOL,
                                                   "profile": PROFILE}, token=AGENT_TOKEN)
status, who = call("GET", "/api/v1/presence", token=AGENT_TOKEN)
me = next((t for t in who.get("telescopes", []) if t["id"] == AGENT_ID), None)
case("a check-in that says nothing keeps what was said before",
     me is not None and me["target"] == "M42", str(me))
# The chart shows the name the rig gives itself - its equipment profile -
# over the "Telescope 1" it was enrolled under.
call("POST", "/api/v1/agent/hello", {"protocol": collab.PROTOCOL, "profile": PROFILE,
                                     "presence": {"telescope": "fsq106"}}, token=AGENT_TOKEN)
status, who = call("GET", "/api/v1/presence", token=AGENT_TOKEN)
me = next((t for t in who.get("telescopes", []) if t["id"] == AGENT_ID), None)
case("a telescope on the chart carries the name its rig gives itself",
     me is not None and me["name"] == "fsq106" and me["enrolledAs"] == "Telescope 1", str(me))

# ------------------------------------------- the coordinator's night rules
status, ruled = call("POST", "/api/v1/projects", {
    "name": "High and dark",
    "region": {"ra": 100.0, "dec": 10.0, "width": 1.0, "height": 1.0, "rotation": 0.0},
    "requirements": {"filters": {"L": None}, "minAltitude": 40,
                     "minMoonSeparation": 50},
    "goals": {"L": 2.0},
}, token=ADMIN)
case("a project can carry an altitude floor and a Moon distance", status == 200)
status, listing = call("GET", "/api/v1/projects")
shown = next((p for p in listing["projects"] if p["id"] == ruled["project"]["id"]), {})
wants = (shown.get("payload") or {}).get("requirements") or {}
case("...and every rig browsing it sees them",
     wants.get("minAltitude") == 40.0 and wants.get("minMoonSeparation") == 50.0,
     str(wants))

# A join that says which night it is and what the Moon is doing is dealt
# for that night with the Moon in mind, so the first deal is the one the
# night keeps - rather than a blind deal that the first poll replaces under
# a sequence already shooting it.
_, moonjoin = call("POST", "/api/v1/projects", {
    "name": "Join under the Moon", "region": {"ra": 80.0, "dec": 30.0, "width": 10.0, "height": 6.0},
    "requirements": {"filters": {"OIII": 3.0, "Ha": 3.0}},
    "goals": {"OIII": 6.0, "Ha": 6.0}}, token=ADMIN)
MOONJOIN = moonjoin["project"]["id"]
_, joined_bright = call("POST", f"/api/v1/agent/projects/{MOONJOIN}/join",
                        {"night": "2026-12-30", "moon": 0.9, "moonUp": 0.9}, token=NB[2][1])
case("a join that carries the night and a bright Moon is dealt narrowband at once",
     joined_bright["task"]["visit"].get("filter") == "H"
     and joined_bright["task"].get("assignedNight") == "2026-12-30",
     str(joined_bright["task"]["visit"]))
held_after = held_on(NB[2][1], "2026-12-30", MOONJOIN, "&moon=0.9&moonUp=0.9")
case("...and the first poll of that night keeps it",
     held_after["version"] == joined_bright["task"]["version"]
     and held_after["visit"].get("filter") == "H")

# ------------------------------------------- progress is depth everywhere
print("\n-- progress: the goal at every point of the field --")

# Ten hours on one corner of a mosaic is not two thirds of a ten-hour goal.
# Progress is the share of the field at the goal depth, so a frame-sized
# patch at full depth is a small fraction, and the whole region at depth
# is one, whatever the hours add up to.
_, deep = call("POST", "/api/v1/projects", {
    "name": "Depth everywhere", "region": {"ra": 120.0, "dec": 20.0, "width": 12.0, "height": 6.0},
    "requirements": {"filters": {"Ha": 3.0}}, "goals": {"Ha": 2.0}}, token=ADMIN)
DEEP = deep["project"]["id"]
_, deep_join = call("POST", f"/api/v1/agent/projects/{DEEP}/join", {}, token=NB[0][1])
deep_task = deep_join["task"]


def progress_of(project_id):
    _, listing_ = call("GET", "/api/v1/agent/projects", token=NB[0][1])
    return next(p for p in listing_["projects"] if p["id"] == project_id)


empty_p = progress_of(DEEP)["progress"]
case("with nothing shot, nothing of the field is at goal",
     empty_p.get("H", {}).get("atGoal") == 0.0 and empty_p["H"]["average"] == 0.0, str(empty_p))

# Two hours (the whole goal) on a 2 x 2 degree patch: a sixth of the area.
call("POST", "/api/v1/agent/report", {"contributions": [{
    "task": deep_task["id"], "night": "2026-12-20", "panel": "1", "filterName": "Ha",
    "frames": 24, "seconds": 7200, "exposure": 300,
    "footprint": {"ra": 120.0, "dec": 20.0, "width": 2.0, "height": 2.0},
    "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0}]}, token=NB[0][1])
patch = progress_of(DEEP)
case("the goal on one patch is that patch's share of the field, not the goal's share of the hours",
     0.03 <= patch["progress"]["H"]["atGoal"] <= 0.08 and patch["collected"]["H"] == 2.0,
     f'{patch["progress"]["H"]} vs {patch["collected"]["H"]}h collected')
case("...and the thinnest part of the field is still empty",
     patch["progress"]["H"]["thinnest"] == 0.0)

# The whole region at the goal, from two half-depth passes: done.
for night in ("2026-12-21", "2026-12-22"):
    call("POST", "/api/v1/agent/report", {"contributions": [{
        "task": deep_task["id"], "night": night, "panel": "0", "filterName": "Ha",
        "frames": 12, "seconds": 3600, "exposure": 300,
        "footprint": {"ra": 120.0, "dec": 20.0, "width": 12.0, "height": 6.0},
        "scale": 2.0, "hfr": 2.5, "guideRms": 0.7, "bandpass": 3.0}]}, token=NB[0][1])
whole = progress_of(DEEP)["progress"]["H"]
case("the whole field at the goal is done, however the hours add up",
     whole["atGoal"] == 1.0 and whole["average"] >= 0.99 and whole["thinnest"] >= 0.9, str(whole))

# The map itself: the cells of the region with everybody's seconds on each,
# per filter, for the picture on the Plan tab.
status, depth_map = call("GET", f"/api/v1/agent/projects/{DEEP}/depth", token=NB[0][1])
case("a participant can fetch the depth map of a project",
     status == 200 and depth_map["columns"] * depth_map["rows"] == len(depth_map["cells"])
     and len(depth_map["seconds"]["H"]) == len(depth_map["cells"]),
     f'{depth_map.get("columns")}x{depth_map.get("rows")}, {len(depth_map.get("cells", []))} cells')
case("...and every cell carries the hours the whole field was given",
     all(s >= 2 * 3600 * 0.9 for s in depth_map["seconds"]["H"])
     and depth_map["progress"]["H"]["atGoal"] == 1.0,
     f'min {min(depth_map["seconds"]["H"]):.0f}s')

# ------------------------------------------------- the N.I.N.A. plugin feed
print("\n-- the server as a N.I.N.A. plugin repository --")

# N.I.N.A. is given `<url>/nina` as a plugin repository and fetches
# `<url>/nina/plugins/manifests`: a JSON list of manifests, each naming the
# zip to download. The zip comes from the same server.
status, feed = call("GET", "/nina/plugins/manifests")
case("the server offers the plugin manifests as a list, the newest of each plugin only",
     status == 200 and isinstance(feed, list) and len(feed) == 1
     and feed[0]["Name"] == "Starfront Collaboration" and feed[0]["Version"]["Major"] == "1",
     str(feed)[:120])
case("...and health says which releases are on offer",
     "Starfront Collaboration 1.0.0.0" in call("GET", "/api/v1/health")[1].get("ninaPlugins", []))
request = urllib.request.Request(feed[0]["Installer"]["URL"], method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    zipped = response.read(), response.headers.get("Content-Type", "")
case("the zip the manifest points at downloads from the server",
     zipped[0].startswith(b"PK") and "zip" in zipped[1], zipped[1])
for wrong in ("notes.txt", "..%2Fcollab.sqlite", "nothing.zip", ".zip"):
    status, _ = call("GET", f"/nina/download/{wrong}")
    if status != 404:
        break
case("...and anything that is not a release is not served", status == 404)
request = urllib.request.Request(f"{BASE}/nina/changelog", method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    log = response.read().decode("utf-8")
case("the plugin's change log is served for the manifests' link", "## 1.0.0.0" in log)
request = urllib.request.Request(f"{BASE}/nina/guide", method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    guide = response.read(), response.headers.get("Content-Type", ""), response.headers.get("Content-Disposition", "")
case("the plugin's guide is served as a PDF to read in the browser",
     guide[0].startswith(b"%PDF") and "pdf" in guide[1] and "inline" in guide[2], f"{guide[1]} {guide[2]}")

# ---------------------------------------------------- the Starfront download
print("\n-- the download page --")

# The source is private; the installer is handed out from the server. The
# newest version is the download, by number and not by spelling, so 0.2.35
# beats 0.2.9.
status, latest = call("GET", "/download/latest.json")
case("latest.json names the newest installer by version number",
     status == 200 and latest["version"] == "0.2.35" and latest["file"] == "Starfront-Setup-0.2.35.exe"
     and latest["url"] == "/download/Starfront-Setup-0.2.35.exe" and latest["size"] == 16, str(latest))
case("...and health says which version is on offer",
     call("GET", "/api/v1/health")[1].get("download") == "0.2.35")
request = urllib.request.Request(f"{BASE}/download/latest", method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    fetched = response.read(), response.headers.get("Content-Disposition", ""), response.url
case("/download/latest fetches that installer, named as a file to save",
     fetched[0].startswith(b"MZ-new") and "Starfront-Setup-0.2.35.exe" in fetched[1]
     and fetched[2].endswith("/download/Starfront-Setup-0.2.35.exe"), str(fetched[1:]))
request = urllib.request.Request(f"{BASE}/download/Starfront-Setup-0.2.9.exe", method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    older = response.read()
case("an older installer's own link keeps working", older.startswith(b"MZ-old"))
request = urllib.request.Request(f"{BASE}/download", method="GET")
with urllib.request.urlopen(request, timeout=10) as response:
    page = response.read().decode("utf-8"), response.headers.get("Content-Type", "")
case("the download page offers the newest one, with its size, and says where N.I.N.A. users go",
     "text/html" in page[1] and 'href="/download/Starfront-Setup-0.2.35.exe"' in page[0]
     and "Download Starfront 0.2.35 for Windows" in page[0] and f"{BASE}/nina" in page[0]
     and "0.2.9" not in page[0], page[1])
for wrong in ("secrets.txt", "..%2Fcollab.sqlite", "Starfront-Setup-9.9.9.exe", "Starfront-Setup-.exe",
              "Starfront-Setup-0.2.35.exe.bak"):
    status, _ = call("GET", f"/download/{wrong}")
    if status != 404:
        break
case("...and nothing but installers is served from the folder", status == 404, f"{wrong} -> {status}")

# ============================ the worker and hub binaries (collab-worker, collab-hub)
print("\n-- the worker and hub binaries --")


def fetch(path):
    """(status, headers, body) for any path, with no redirects and no exception."""
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


MANIFEST_TEXT = b'{"version": "1.2.3", "contract": "1.27.0", "assets": {}}'
for product in ("collab-worker", "collab-hub"):
    folder = DOWNLOAD_DIR / product
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "latest.json").write_bytes(MANIFEST_TEXT)
    (folder / "latest.json.sig").write_bytes(b"ed25519-signature-" + product.encode())
    (folder / f"{product}-1.2.3-linux-x64.tar.gz").write_bytes(b"\x1f\x8b" + b"tar-" + product.encode())
    (folder / f"{product}-1.2.3-windows-x64.zip").write_bytes(b"PK\x03\x04" + b"zip-" + product.encode())
    (folder / f"{product}-1.2.3-macos-arm64.zip").write_bytes(b"PK\x03\x04" + b"mac-" + product.encode())
    other = "collab-hub" if product == "collab-worker" else "collab-worker"
    prefix = f"/download/{product}"

    status, headers, body = fetch(f"{prefix}/latest.json")
    case(f"{product}: latest.json is served as JSON and never cached",
         status == 200 and body == MANIFEST_TEXT and headers.get("Content-Type", "").startswith("application/json")
         and headers.get("Cache-Control") == "no-cache" and headers.get("Content-Length") == str(len(MANIFEST_TEXT)),
         f"{status} {headers.get('Content-Type')} {headers.get('Cache-Control')}")
    status, headers, body = fetch(f"{prefix}/latest.json.sig")
    case(f"{product}: latest.json.sig is served as bytes and never cached",
         status == 200 and body == b"ed25519-signature-" + product.encode()
         and headers.get("Content-Type") == "application/octet-stream"
         and headers.get("Cache-Control") == "no-cache" and headers.get("Content-Length") == str(len(body)),
         f"{status} {headers.get('Content-Type')} {headers.get('Cache-Control')}")
    for archive in (f"{product}-1.2.3-linux-x64.tar.gz", f"{product}-1.2.3-windows-x64.zip",
                    f"{product}-1.2.3-macos-arm64.zip"):
        status, headers, body = fetch(f"{prefix}/{archive}")
        case(f"{product}: {archive} is served as bytes, immutable for a year, named as a file",
             status == 200 and body.endswith(product.encode())
             and headers.get("Content-Type") == "application/octet-stream"
             and headers.get("Cache-Control") == "public, max-age=31536000, immutable"
             and headers.get("Content-Length") == str(len(body))
             and archive in (headers.get("Content-Disposition") or ""),
             f"{status} {headers.get('Content-Type')} {headers.get('Cache-Control')}")
    for wrong in ("evil.sh", f"{product}-1.0-macos.zip", "../latest.json", "..%2Flatest.json", "%2e%2e%2flatest.json",
                  "collab-hub-1.2.3-plan9.zip", f"{product}-1.2.3-plan9.zip", f"{product}-1.2.3-linux-x64.rar",
                  f"{other}-1.2.3-linux-x64.tar.gz", "latest.json.bak", "latest.json%0A"):
        status, _, _ = fetch(f"{prefix}/{wrong}")
        if status != 404:
            break
    case(f"{product}: only the pattern is served; {wrong} is a 404", status == 404, f"{wrong} -> {status}")
    # The manifest is the one thing a release may not have produced yet.
    (folder / "latest.json").rename(folder / "latest.json.moved")
    status, _, _ = fetch(f"{prefix}/latest.json")
    (folder / "latest.json.moved").rename(folder / "latest.json")
    case(f"{product}: a missing latest.json is a 404", status == 404, str(status))

# ======================================== a colour camera with a dual-band filter
print("\n-- a colour camera with a dual-band filter --")

# A project wants channels; a filter delivers one or more of them. A one-shot
# colour camera behind a UV/IR cut delivers red, green and blue in one sub,
# and behind an L-eXtreme delivers Ha and OIII at once. It used to be told it
# carried none of the wanted filters, because nothing knew what an
# "L-eXtreme" was, and a night through it would have counted for one
# unknown filter.
_, osc_made = call("POST", "/api/v1/agents", {"name": "osc"}, token=ADMIN)
OSC_TOKEN = osc_made["token"]
status, _ = call("POST", "/api/v1/agent/hello", {
    "protocol": collab.PROTOCOL,
    "profile": {"name": "osc", "focalLength": 400.0, "pixelSize": 3.76, "sensorWidth": 6248,
                "sensorHeight": 4176, "binning": 1, "colour": True, "hoursPerNight": 4.0,
                "filters": {"UV/IR cut": None, "L-eXtreme": 7.0},
                "channels": {"UV/IR cut": ["OSC-RGB"], "L-eXtreme": ["OSC-Ha+OIII"]},
                "exposures": {"UV/IR cut": 120.0, "L-eXtreme": 300.0}, "bortle": 2}},
    token=OSC_TOKEN)
case("a colour camera says what each of its filters delivers, and its sky", status == 200)
_, started = call("POST", "/api/v1/projects", {
    "name": "Veil in everything", "kind": "single",
    "region": {"ra": 313.0, "dec": 31.0, "width": 2.0, "height": 1.5},
    # Mono channels and, kept apart from them, the two colour-camera bands.
    "requirements": {"filters": {"H": 7.0, "O": 7.0, "S": 7.0, "R": None, "G": None, "B": None,
                                 "OSC-RGB": None, "OSC-Ha+OIII": 7.0},
                     "acceptOscRgb": True, "acceptOscNarrowband": True, "maxBortle": 4},
    "goals": {"H": 10.0, "O": 10.0, "S": 10.0, "R": 4.0, "G": 4.0, "B": 4.0,
              "OSC-RGB": 4.0, "OSC-Ha+OIII": 10.0}}, token=ADMIN)
EVERYTHING = started["project"]["id"]
_, browse = call("GET", "/api/v1/agent/projects", token=OSC_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == EVERYTHING)
filters_check = next(c for c in listing["compatibility"]["checks"] if c["check"] == "filters")
case("...and is told it can give the two OSC bands, through which filters, under a sky that qualifies",
     listing["compatibility"]["ok"] and "L-eXtreme gives OSC-Ha+OIII" in filters_check["detail"]
     and "UV/IR cut gives OSC-RGB" in filters_check["detail"]
     and any(c["check"] == "sky" and c["ok"] for c in listing["compatibility"]["checks"]),
     filters_check["detail"])
status, joined = call("POST", f"/api/v1/agent/projects/{EVERYTHING}/join", {"night": "2026-10-12"},
                      token=OSC_TOKEN)
dealt = joined["task"]
by_name = {f["filter"]: f for f in dealt["filters"]}
case("joining deals it its own filters, each with the band it delivers, at its own exposures",
     status == 200 and sorted(by_name) == ["L-eXtreme", "UV/IR cut"]
     and by_name["L-eXtreme"]["channels"] == ["OSC-Ha+OIII"]
     and by_name["UV/IR cut"]["channels"] == ["OSC-RGB"]
     and by_name["L-eXtreme"]["exposure"] == 300.0 and by_name["UV/IR cut"]["exposure"] == 120.0,
     str(dealt["filters"]))
frames = (dealt.get("visit") or {}).get("frames") or {}
case("...and tonight's frames are named by those filters",
     bool(frames) and set(frames) <= {"L-eXtreme", "UV/IR cut"}, str(frames))

NIGHT = {"task": dealt["id"], "night": "2026-10-12", "panel": "1", "filterName": "L-eXtreme",
         "channels": ["OSC-Ha+OIII"], "frames": 12, "seconds": 3600, "exposure": 300,
         "footprint": {"ra": 313.0, "dec": 31.0, "width": 2.0, "height": 1.5},
         "scale": 1.94, "focalLength": 400.0, "colour": True, "bandpass": 7.0, "hfr": 3.0, "bortle": 2}
status, answer = call("POST", "/api/v1/agent/report", {"contributions": [NIGHT]}, token=OSC_TOKEN)
case("a night through the dual-band is accepted",
     status == 200 and answer["recorded"][0]["accepted"], str(answer["recorded"][0]["verdict"]))
_, browse = call("GET", "/api/v1/agent/projects", token=OSC_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == EVERYTHING)
got = listing["collected"]
case("...and is logged as OSC-Ha+OIII, apart from the mono Ha and OIII",
     abs(got.get("OSC-Ha+OIII", 0) - 1.0) < 0.01 and not got.get("H") and not got.get("O"), str(got))
case("...with the progress measured per band",
     listing["progress"]["OSC-Ha+OIII"]["average"] > 0 and listing["progress"]["H"]["average"] == 0
     and listing["progress"]["OSC-RGB"]["average"] == 0,
     str({k: v["average"] for k, v in listing["progress"].items()}))
status, answer = call("POST", "/api/v1/agent/report",
                      {"contributions": [{**NIGHT, "frames": 24, "seconds": 7200}]}, token=OSC_TOKEN)
_, browse = call("GET", "/api/v1/agent/projects", token=OSC_TOKEN)
got = next(p for p in browse["projects"] if p["id"] == EVERYTHING)["collected"]
case("...reported again as the night grew, it is the larger figure and not the sum",
     abs(got.get("OSC-Ha+OIII", 0) - 2.0) < 0.01, str(got))
status, answer = call("POST", "/api/v1/agent/report",
                      {"contributions": [{**NIGHT, "panel": "2", "bortle": 6}]}, token=OSC_TOKEN)
case("a night under a brighter sky than the project wants is refused, and told",
     status == 200 and not answer["recorded"][0]["accepted"]
     and "Bortle 6" in answer["recorded"][0]["verdict"]["summary"],
     answer["recorded"][0]["verdict"]["summary"])
strict = call("POST", "/api/v1/projects", {
    "name": "Narrow only", "kind": "single",
    "region": {"ra": 313.0, "dec": 31.0, "width": 2.0, "height": 1.5},
    "requirements": {"filters": {"OSC-Ha+OIII": 5.0}}, "goals": {"OSC-Ha+OIII": 10.0}}, token=ADMIN)[1]
_, browse = call("GET", "/api/v1/agent/projects", token=OSC_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == strict["project"]["id"])
case("a project wanting 5 nm turns the 7 nm dual-band away, naming it",
     not listing["compatibility"]["ok"] and "L-eXtreme is 7 nm" in listing["compatibility"]["summary"],
     listing["compatibility"]["summary"])
mono_only = call("POST", "/api/v1/projects", {
    "name": "Mono only", "kind": "single",
    "region": {"ra": 313.0, "dec": 31.0, "width": 2.0, "height": 1.5},
    "requirements": {"filters": {"H": 7.0, "O": 7.0}}, "goals": {"H": 10.0, "O": 10.0}}, token=ADMIN)[1]
_, browse = call("GET", "/api/v1/agent/projects", token=OSC_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == mono_only["project"]["id"])
case("a project listing mono channels only has nothing for a colour camera, and says so",
     not listing["compatibility"]["ok"] and "takes no one-shot colour data" in listing["compatibility"]["summary"],
     listing["compatibility"]["summary"])

# ------------------------------------------------- the files: lights collected
print("\n-- collecting the lights --")


def raw(method, path, data=None, headers=None, expect_json=True):
    """A request with a body that is not JSON, and whatever headers."""
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return response.status, (json.loads(body.decode() or "{}") if expect_json else body), response.headers
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body.decode() or "{}"), exc.headers
        except ValueError:
            return exc.code, body, exc.headers


FITS = b"SIMPLE  =                    T / conforms to FITS" + b" " * 30
FITS = FITS.ljust(2880, b" ") + bytes(2880)        # one header block, one data block

_, _, _, polled = sign_in("code-bob")
BOB2 = polled["token"]
status, made = call("POST", "/api/v1/projects", {
    "name": "Rosette, files collected", "kind": "single",
    "region": {"ra": 97.98, "dec": 4.95, "width": 2.0, "height": 1.5},
    "requirements": {"filters": {"H": None}}, "goals": {"H": 10.0},
    "collectFiles": True}, token=BOB2)
FILES_PROJECT = made["project"]["id"]
case("a project can be started with its lights collected",
     status == 200 and made["project"]["payload"]["collectFiles"] is True)
_, browse = call("GET", "/api/v1/agent/projects", token=AGENT_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == FILES_PROJECT)
case("...and the listing says so, with where to send them",
     listing["collectFiles"] is True and listing["filesUrl"].endswith(f"/files#{FILES_PROJECT}"),
     listing["filesUrl"])

# ------------------------------------------- live stacking: the join code
# A collaboration can name the Rapid Stacker Collab project that stacks its
# lights as they land. The code is a credential: it is stored canonical,
# refused when it cannot be real, and handed out only to telescopes on the
# collaboration and to the owner's own.
print("\n-- live stacking: the join code --")
status, made = call("POST", "/api/v1/projects", {
    "name": "Orion, live-stacked", "region": {"ra": 84.0, "dec": 0.0,
                                               "width": 16.0, "height": 8.0},
    "requirements": {"filters": {"Ha": 3.0}}, "goals": {"Ha": 10.0},
    "stackingJoinCode": " h4rt 92bk-wnpc "}, token=BOB2)
STACKED = made["project"]["id"]
case("a project can be started with a stacking join code, stored canonical",
     status == 200 and made["project"]["payload"]["stackingJoinCode"] == "H4RT-92BK-WNPC",
     str(made)[:200])
status, answer = call("POST", "/api/v1/projects", {
    "name": "typo", "region": {"ra": 84.0, "dec": 0.0, "width": 16.0, "height": 8.0},
    "stackingJoinCode": "H4RT-92BK-WNP1"}, token=BOB2)
case("a code that cannot be real is refused", status == 400 and "twelve characters" in answer["detail"],
     str(answer)[:200])
status, answer = call("POST", "/api/v1/projects", {
    "name": "short", "region": {"ra": 84.0, "dec": 0.0, "width": 16.0, "height": 8.0},
    "stackingJoinCode": "H4RT-92BK"}, token=BOB2)
case("...and so is one that is too short", status == 400)

_, bobs = call("POST", "/api/v1/agents", {"name": "Bob's rig"}, token=BOB2)
_, browse = call("GET", "/api/v1/agent/projects", token=bobs["token"])
listing = next(p for p in browse["projects"] if p["id"] == STACKED)
case("the owner's own telescope is handed the code without joining",
     listing["stackingLinked"] is True and listing["stackingJoinCode"] == "H4RT-92BK-WNPC",
     str(listing.get("stackingJoinCode")))
_, browse = call("GET", "/api/v1/agent/projects", token=AGENT_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == STACKED)
case("a telescope that is only browsing learns that the lights are stacked, not the code",
     listing["stackingLinked"] is True and listing["stackingJoinCode"] == "",
     str(listing.get("stackingJoinCode")))
STACKER_TOKEN, _ = enrol("stacker", 389.0, 9576, 6388, 6.0)
status, _ = call("POST", f"/api/v1/agent/projects/{STACKED}/join", {}, token=STACKER_TOKEN)
_, browse = call("GET", "/api/v1/agent/projects", token=STACKER_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == STACKED)
case("a telescope on the collaboration is handed the code",
     status == 200 and listing["stackingJoinCode"] == "H4RT-92BK-WNPC",
     f"join {status}, code {listing.get('stackingJoinCode')!r}")
_, browse = call("GET", "/api/v1/agent/projects", token=AGENT_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == FILES_PROJECT)
case("a collaboration without one says so", listing["stackingLinked"] is False
     and listing["stackingJoinCode"] == "")

status, changed = call("PUT", f"/api/v1/projects/{STACKED}", {"notes": "still stacked"}, token=BOB2)
case("an edit that leaves the code out leaves it alone",
     status == 200 and changed["project"]["payload"]["stackingJoinCode"] == "H4RT-92BK-WNPC")
status, changed = call("PUT", f"/api/v1/projects/{STACKED}", {"stackingJoinCode": "wnpc-h4rt-92bk"}, token=BOB2)
case("...one that sends a new code replaces it, canonical",
     status == 200 and changed["project"]["payload"]["stackingJoinCode"] == "WNPC-H4RT-92BK")
status, _ = call("PUT", f"/api/v1/projects/{STACKED}", {"stackingJoinCode": "nope"}, token=BOB2)
_, browse = call("GET", "/api/v1/agent/projects", token=STACKER_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == STACKED)
case("...a bad one is refused and the old one stands",
     status == 400 and listing["stackingJoinCode"] == "WNPC-H4RT-92BK")
status, changed = call("PUT", f"/api/v1/projects/{STACKED}", {"stackingJoinCode": ""}, token=BOB2)
_, browse = call("GET", "/api/v1/agent/projects", token=STACKER_TOKEN)
listing = next(p for p in browse["projects"] if p["id"] == STACKED)
case("...and a blank one clears it", status == 200 and changed["project"]["payload"]["stackingJoinCode"] == ""
     and listing["stackingLinked"] is False and listing["stackingJoinCode"] == "")

status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_001.fits", FITS,
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": str(len(FITS))})
case("a signed-in person can upload a light", status == 200 and answer["file"]["name"] == "M31_Ha_001.fits"
     and answer["file"]["bytes"] == len(FITS) and answer["already"] is False, str(answer)[:200])
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_001.fits", FITS,
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": str(len(FITS))})
case("...the same file again is not taken twice", status == 200 and answer["already"] is True)
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/notes.txt", b"hello",
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": "5"})
case("a file that is not a frame is refused", status == 400 and "not a frame" in answer["detail"])
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/fake.fits", b"JFIF" * 100,
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": "400"})
case("...and so is a .fits that is not a FITS inside", status == 400 and "does not start like" in answer["detail"])
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/../../escape.fits", FITS,
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": str(len(FITS))})
case("a path in the name goes nowhere", status in (200, 400, 404, 405)
     and not (DATA / "escape.fits").exists() and not (DATA.parent / "escape.fits").exists()
     and not (DATA / "files" / "escape.fits").exists(),
     f"{status} {str(answer)[:80]}")
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_002.fits", FITS,
                        {"Content-Length": str(len(FITS))})
case("nobody uploads without signing in", status == 401)
status, answer, _ = raw("PUT", f"/api/v1/projects/{ORION}/files/M42.fits", FITS,
                        {"Authorization": f"Bearer {ALICE}", "Content-Length": str(len(FITS))})
case("a project not collecting files takes none (unless it is yours)",
     status in (409, 200), str(status))

# A telescope's token uploads under its owner: that is how the plugin will
# send each light as it is saved.
_, mine_rig = call("POST", "/api/v1/agents", {"name": "Bob's rig"}, token=BOB2)
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_002.fits", FITS,
                        {"Authorization": f"Bearer {mine_rig['token']}", "Content-Length": str(len(FITS))})
case("a telescope's token uploads under its owner, named after the telescope",
     status == 200 and answer["file"]["telescope"] == "Bob's rig" and answer["file"]["uploader"] == "Bob",
     str(answer)[:200])

_, listed = call("GET", f"/api/v1/projects/{FILES_PROJECT}/files", token=ALICE)
case("the coordinator (Alice owns the server) sees everybody's files",
     listed["owned"] is True and sorted(f["name"] for f in listed["files"]) == ["M31_Ha_001.fits", "M31_Ha_002.fits"]
     and listed["bytes"] == 2 * len(FITS), str([f["name"] for f in listed["files"]]))
_, _, _, polled = sign_in("code-bob")
BOB3 = polled["token"]
_, listed_bob = call("GET", f"/api/v1/projects/{FILES_PROJECT}/files", token=BOB3)
case("the project's owner sees them too", listed_bob["owned"] is True and len(listed_bob["files"]) == 2)
_, _, landed, polled = sign_in("code-alice")
ALICE = polled["token"]            # a program sign-in replaces the program token
status, got, _ = raw("GET", f"/api/v1/projects/{FILES_PROJECT}/files/1001/light/M31_Ha_001.fits", None,
                     {"Authorization": f"Bearer {BOB3}"}, expect_json=False)
case("...and can download each one", status == 200 and got == FITS)
status, got, _ = raw("GET", f"/api/v1/projects/{FILES_PROJECT}/files/1002/light/M31_Ha_002.fits", None,
                     {"Authorization": f"Bearer {polled['token']}"}, expect_json=False)
case("a person may download their own", status == 200 and got == FITS)
case("...and files live under the files folder by project, person and kind",
     (DATA / "files" / FILES_PROJECT / "1002" / "lights" / "M31_Ha_002.fits").is_file())

# Calibration frames: told apart by the name N.I.N.A. gave them, or by the
# kind the uploader chose, each in its own folder.
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/2026-10-12_FLAT_Ha_0001.fits", FITS,
                        {"Authorization": f"Bearer {BOB3}", "Content-Length": str(len(FITS))})
case("a flat is told from its name and filed as one",
     status == 200 and answer["file"]["kind"] == "flat"
     and (DATA / "files" / FILES_PROJECT / "1002" / "flats" / "2026-10-12_FLAT_Ha_0001.fits").is_file(),
     str(answer)[:160])
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/2026-10-12_DARKFLAT_Ha_0001.fits", FITS,
                        {"Authorization": f"Bearer {BOB3}", "Content-Length": str(len(FITS))})
case("...a dark flat is not read as a dark", status == 200 and answer["file"]["kind"] == "darkflat")
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/frame_0007.fits?kind=bias", FITS,
                        {"Authorization": f"Bearer {BOB3}", "Content-Length": str(len(FITS))})
case("...and an unmarked name takes the kind the uploader chose",
     status == 200 and answer["file"]["kind"] == "bias")
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/frame_0007.fits?kind=plate", FITS,
                        {"Authorization": f"Bearer {BOB3}", "Content-Length": str(len(FITS))})
case("...a kind that is not one of the five is refused", status == 400)
_, listed = call("GET", f"/api/v1/projects/{FILES_PROJECT}/files", token=ALICE)
case("the listing says what each file is",
     sorted((f["name"], f["kind"]) for f in listed["files"]) == sorted([
         ("M31_Ha_001.fits", "light"), ("M31_Ha_002.fits", "light"),
         ("2026-10-12_FLAT_Ha_0001.fits", "flat"), ("2026-10-12_DARKFLAT_Ha_0001.fits", "darkflat"),
         ("frame_0007.fits", "bias")]),
     str([(f["name"], f["kind"]) for f in listed["files"]]))
status, info, _ = raw("GET", "/api/v1/files/projects", None, {"Authorization": f"Bearer {ALICE}"})
project_row = next(p for p in info["projects"] if p["id"] == FILES_PROJECT)
case("...and the page's list counts them by kind",
     project_row["kinds"] == {"light": 2, "flat": 1, "darkflat": 1, "bias": 1}, str(project_row["kinds"]))
status, _ = call("DELETE", f"/api/v1/projects/{FILES_PROJECT}/files/frame_0007.fits?kind=bias", token=BOB3)
case("a calibration frame comes off by its kind", status == 200
     and not (DATA / "files" / FILES_PROJECT / "1002" / "bias" / "frame_0007.fits").exists())
status, _ = call("DELETE", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_002.fits", token=BOB3)
_, listed = call("GET", f"/api/v1/projects/{FILES_PROJECT}/files", token=ALICE)
case("a person can take their own file off again",
     status == 200 and "M31_Ha_002.fits" not in [f["name"] for f in listed["files"]]
     and not (DATA / "files" / FILES_PROJECT / "1002" / "lights" / "M31_Ha_002.fits").exists())
_, health = call("GET", "/api/v1/health")
case("health reports what the files folder holds and the room left",
     # Alice's M31_Ha_001 here, the M42 she put on Orion as the owner, and
     # Bob's flat and dark flat.
     health["files"]["count"] == 4 and health["files"]["bytes"] == 4 * len(FITS)
     and health["files"]["free"] > 0,
     str(health["files"]))

# The browser: a sign-in begun from the files page comes back to it with a
# cookie, and that cookie is a credential of its own - Alice's program token
# still works afterwards.
opener = urllib.request.build_opener(_NoRedirect())
try:
    opener.open(urllib.request.Request(f"{BASE}/files/signin", method="GET"), timeout=10)
    raise AssertionError("expected a redirect")
except urllib.error.HTTPError as hop:
    to_start = hop.headers.get("Location")
case("the files page's sign-in begins a login and goes to Discord's start page",
     to_start and to_start.startswith("/auth/discord/start?code="), str(to_start))
try:
    opener.open(urllib.request.Request(BASE + to_start, method="GET"), timeout=10)
    raise AssertionError("expected a redirect")
except urllib.error.HTTPError as hop:
    to_discord = hop.headers.get("Location") + "&who=code-alice"
try:
    opener.open(urllib.request.Request(to_discord, method="GET"), timeout=10)
    raise AssertionError("expected a redirect")
except urllib.error.HTTPError as hop:
    back = hop.headers.get("Location")
try:
    opener.open(urllib.request.Request(back, method="GET"), timeout=10)
    raise AssertionError("expected a redirect back to the files page")
except urllib.error.HTTPError as hop:
    hop_code = hop.code
    landed_at = hop.headers.get("Location")
    cookie = hop.headers.get("Set-Cookie") or ""
case("...and Discord's callback sends the browser back to the files page with a cookie",
     hop_code == 303 and landed_at == "/files" and cookie.startswith("starfront_web=") and "HttpOnly" in cookie,
     f"{hop_code} {landed_at} {cookie[:40]}")
jar = cookie.split(";", 1)[0]
status, info, _ = raw("GET", "/api/v1/files/projects", None, {"Cookie": jar})
case("the cookie signs the page in", status == 200 and info["signedIn"] and info["name"] == "Alice"
     and any(p["id"] == FILES_PROJECT for p in info["projects"]), str(info)[:200])
status, answer, _ = raw("PUT", f"/api/v1/projects/{FILES_PROJECT}/files/M31_Ha_003.fits", FITS,
                        {"Cookie": jar, "Content-Length": str(len(FITS))})
case("...and uploads on it", status == 200 and answer["file"]["name"] == "M31_Ha_003.fits")
_, me = call("GET", "/api/v1/auth/me", token=ALICE)
case("signing in on the files page did not sign Alice's program out", me.get("name") == "Alice")
status, page, _ = raw("GET", "/files", None, {}, expect_json=False)
case("the files page is served", status == 200 and b"Starfront files" in page)
status, _, _ = raw("POST", "/files/signout", b"", {"Cookie": jar})
status, info, _ = raw("GET", "/api/v1/files/projects", None, {"Cookie": jar})
case("signing out on the page forgets the cookie's token", not info["signedIn"])

# ------------------------------------------- live stacking: hubs
print("\n-- live stacking: hubs --")
from server import hubs as hubs_module                           # noqa: E402

status, _ = call("POST", "/api/v1/hubs", {"name": "x"})
case("registering a hub needs somebody signed in", status == 401)
status, _ = call("POST", "/api/v1/hubs", {"name": "x"}, token=AGENT_TOKEN)
case("...and a telescope's token is not a person", status == 401)
status, reg = call("POST", "/api/v1/hubs",
                   {"name": "Eddie's hub", "lanUrl": "http://10.0.0.5:8790"}, token=BOB3)
HUB, HTOK, HSEC = reg.get("hubId"), reg.get("hubToken", ""), reg.get("grantSecret", "")
case("a person registers a hub and gets an id, a token and a secret once",
     status == 200 and HUB and HTOK.startswith("sfh_") and len(HTOK) == 68
     and len(HSEC) == 43, str(reg)[:120])
status, bad = call("POST", "/api/v1/hubs", {"name": "", "lanUrl": "x"}, token=BOB3)
case("a nameless hub is refused", status == 400)
status, bad = call("POST", "/api/v1/hubs", {"name": "h", "lanUrl": "ftp://x"}, token=BOB3)
case("...and so is a lanUrl that is not http(s)", status == 400)
status, listed = call("GET", "/api/v1/hubs", token=BOB3)
case("the owner lists their hub",
     status == 200 and [h["id"] for h in listed["hubs"]] == [HUB]
     and listed["hubs"][0]["lanUrl"] == "http://10.0.0.5:8790")
case("...and the list never carries the token or the secret",
     HTOK not in json.dumps(listed) and HSEC not in json.dumps(listed)
     and not any(k in listed["hubs"][0] for k in ("token", "grant_secret", "grantSecret", "hubToken")))
case("only a hash of the token is stored",
     server_app.store.hub(HUB)["token"] == hubs_module.hash_token(HTOK) != HTOK
     and server_app.store.hub(HUB)["grant_secret"] == HSEC)
status, other = call("GET", "/api/v1/hubs", token=ALICE)
case("another person does not see it", status == 200 and other["hubs"] == [])
status, _ = call("DELETE", f"/api/v1/hubs/{HUB}", token=ALICE)
case("...and cannot revoke it", status == 404)
status, _ = call("PUT", f"/api/v1/hubs/{HUB}/coordinators", {"coordinators": ["1001"]}, token=ALICE)
case("...or change its coordinators", status == 404)

status, _ = call("POST", f"/api/v1/hubs/{HUB}/heartbeat", {"workers": 2}, token=HTOK)
case("a heartbeat on the hub's token is taken", status == 200)
status, _ = call("POST", f"/api/v1/hubs/{HUB}/heartbeat", {"workers": 2}, token="sfh_" + "0" * 64)
case("...a wrong token is refused", status == 401)
status, _ = call("POST", f"/api/v1/hubs/{HUB}/heartbeat", {"workers": 2}, token=BOB3)
case("...and so is a person's token", status == 401)
status, listed = call("GET", "/api/v1/hubs", token=BOB3)
case("the heartbeat's status and time are kept",
     listed["hubs"][0]["status"] == {"workers": 2} and listed["hubs"][0]["seen"] > 0)

status, got = call("PUT", f"/api/v1/hubs/{HUB}/coordinators",
                   {"coordinators": ["1001", "1001", "1002"]}, token=BOB3)
case("the owner sets the coordinators list (deduplicated, owner implied)",
     status == 200 and got["allowedCoordinators"] == ["1001"], str(got))
status, _ = call("PUT", f"/api/v1/hubs/{HUB}/coordinators", {"coordinators": "1001"}, token=BOB3)
case("...a list that is not a list is refused", status == 400)
_, listed = call("GET", "/api/v1/hubs", token=BOB3)
case("...and the hub reports it", listed["hubs"][0]["allowedCoordinators"] == ["1001"])

# The change feed: pull answers what came after `since`, and waits when
# there is nothing.
s1 = server_app.store.append_change(HUB, "project", "prj_a")
s2 = server_app.store.append_change(HUB, "member", "prj_a/u1")
server_app.store.append_change("hub_elsewhere", "project", "prj_z")
status, got = call("GET", f"/api/v1/hubs/{HUB}/pull?since=0", token=HTOK)
case("pull returns this hub's changes, in order, and the new seq",
     status == 200 and [c["seq"] for c in got["changes"]] == [s1, s2] and got["seq"] == s2
     and got["changes"][0]["kind"] == "project" and got["changes"][0]["key"] == "prj_a", str(got)[:200])
status, got = call("GET", f"/api/v1/hubs/{HUB}/pull?since={s1}", token=HTOK)
case("...only those after `since`", [c["seq"] for c in got["changes"]] == [s2])
began = time.time()
status, got = call("GET", f"/api/v1/hubs/{HUB}/pull?since={s2}&wait=1", token=HTOK)
case("with nothing new it waits, then answers empty at the same seq",
     status == 200 and got == {"changes": [], "seq": s2} and 0.8 < time.time() - began < 3,
     f"{time.time() - began:.1f}s")
late = []
threading.Timer(0.6, lambda: late.append(server_app.store.append_change(HUB, "rig", "u1/r1"))).start()
began = time.time()
status, got = call("GET", f"/api/v1/hubs/{HUB}/pull?since={s2}", token=HTOK)
case("...and a change arriving meanwhile ends the wait early",
     status == 200 and late and got["seq"] == late[0] and time.time() - began < 5,
     f"{time.time() - began:.1f}s")
began = time.time()
status, _ = call("GET", "/api/v1/hubs/other/pull?since=0", token=HTOK)
case("a hub cannot pull another hub's id", status == 401)
status, _ = call("GET", f"/api/v1/hubs/{HUB}/pull?since=0", token=BOB3)
case("...nor a person pull on the hub's behalf", status == 401)
case("pull recorded the hub's cursor", server_app.store.hub(HUB)["cursor"] >= s2)

# Grants.
NOW = 1_800_000_000
hub_row = server_app.store.hub(HUB)
grant = hubs_module.mint_grant(hub_row, "1001", "Alice", "agent_1", "prj_a", NOW)
claims = hubs_module.verify_grant(grant, HSEC, NOW + 10)
case("a grant starts sfg_ and round-trips",
     grant.startswith("sfg_") and claims and claims["hub"] == HUB and claims["sub"] == "1001"
     and claims["name"] == "Alice" and claims["agent"] == "agent_1"
     and claims["project"] == "prj_a" and claims["v"] == 1
     and claims["exp"] == claims["iat"] + 86400 == NOW + 86400, str(claims))
payload, signature = grant[4:].split(".")
import base64                                                    # noqa: E402


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


forged = dict(json.loads(b64d(payload)), sub="1002")
tampered = "sfg_" + b64e(json.dumps(forged, separators=(",", ":")).encode()) + "." + signature
case("a tampered payload is refused", hubs_module.verify_grant(tampered, HSEC, NOW + 10) is None)
flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
case("a tampered signature is refused",
     hubs_module.verify_grant(f"sfg_{payload}.{flipped}", HSEC, NOW + 10) is None)
case("an expired grant is refused (past the leeway)",
     hubs_module.verify_grant(grant, HSEC, NOW + 86400 + 301) is None)
case("...but a few minutes of clock difference is forgiven",
     hubs_module.verify_grant(grant, HSEC, NOW + 86400 + 200) is not None
     and hubs_module.verify_grant(grant, HSEC, NOW - 200) is not None
     and hubs_module.verify_grant(grant, HSEC, NOW - 301) is None)
status, reg_b = call("POST", "/api/v1/hubs", {"name": "Hub B"}, token=BOB3)
case("hub A's grant fails under hub B's secret",
     hubs_module.verify_grant(grant, reg_b["grantSecret"], NOW + 10) is None)
case("garbage is refused without raising",
     all(hubs_module.verify_grant(junk, HSEC, NOW) is None
         for junk in ("", "sfg_", "sfg_.", "sfg_abc.def", "rscu_x", "sfg_" + payload, "sfg_!!.!!")))

status, _ = call("DELETE", f"/api/v1/hubs/{HUB}", token=BOB3)
case("the owner revokes the hub", status == 200)
status, _ = call("POST", f"/api/v1/hubs/{HUB}/heartbeat", {}, token=HTOK)
case("...and its token stops working", status == 401)
_, listed = call("GET", "/api/v1/hubs", token=BOB3)
case("...and it is gone from the list", [h["name"] for h in listed["hubs"]] == ["Hub B"])

# ------------------------------------------- live stacking: the project binding
# A collaboration is stacked on the cloud (a join code) or on a hub. Binding
# one to a hub mints the stacking project the hub will serve, tells the hub,
# and puts a `stacking` block (with a grant, held to the join code's secrecy
# rule) in the listing. The old fields have to keep working for 1.0.0.33.
print("\n-- live stacking: the project binding --")
import re as _re                                                  # noqa: E402
from server.stack import defaults as stack_defaults               # noqa: E402

REGION = {"ra": 84.0, "dec": 0.0, "width": 16.0, "height": 8.0}
_, _, _, polled = sign_in("code-bob")
BOBH = polled["token"]
_, _, _, polled = sign_in("code-carol")
CAROL = polled["token"]
_, _, _, polled = sign_in("code-alice")
ALICE = polled["token"]
status, reg_h = call("POST", "/api/v1/hubs", {"name": "Bind hub", "lanUrl": "http://10.0.0.7:8790"},
                     token=BOBH)
BH, BHSEC, BHTOK = reg_h["hubId"], reg_h["grantSecret"], reg_h["hubToken"]
call("PUT", f"/api/v1/hubs/{BH}/coordinators", {"coordinators": ["1004"]}, token=BOBH)
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since=0&wait=0", token=BHTOK)
SINCE = pulled["seq"]
case("a new hub's feed is empty", pulled["changes"] == [])


def project_in_listing(token, project_id):
    _, browse = call("GET", "/api/v1/agent/projects", token=token)
    return next(p for p in browse["projects"] if p["id"] == project_id)


# Cloud, in the new form: the code is stored both ways.
status, made = call("POST", "/api/v1/projects", {
    "name": "Cloud stacked", "region": REGION, "requirements": {"filters": {"Ha": 3.0}},
    "stacking": {"backend": "cloud", "joinCode": " h4rt 92bk-wnpc "}}, token=BOBH)
CLOUD = made["project"]["id"]
payload = made["project"]["payload"]
case("stacking.backend=cloud stores the join code in the block and as stackingJoinCode",
     status == 200 and payload["stackingJoinCode"] == "H4RT-92BK-WNPC"
     and payload["stacking"]["backend"] == "cloud"
     and payload["stacking"]["joinCode"] == "H4RT-92BK-WNPC", str(payload)[:240])
status, answer = call("POST", "/api/v1/projects", {
    "name": "no code", "region": REGION, "stacking": {"backend": "cloud"}}, token=BOBH)
case("...a cloud binding without a join code is refused", status == 400, str(answer)[:120])
status, answer = call("POST", "/api/v1/projects", {
    "name": "bad code", "region": REGION,
    "stacking": {"backend": "cloud", "joinCode": "H4RT-92BK-WNP1"}}, token=BOBH)
case("...and so is one that cannot be real", status == 400)
status, answer = call("POST", "/api/v1/projects", {
    "name": "bad backend", "region": REGION, "stacking": {"backend": "ftp"}}, token=BOBH)
case("...and an unknown backend", status == 422)
case("a cloud collaboration has no stacking project row", server_app.store.stack_project_of(CLOUD) is None)

# An old payload, with only stackingJoinCode, reads as cloud.
case("_stacking_of reads a payload with only stackingJoinCode as cloud",
     server_app._stacking_of({"stackingJoinCode": "H4RT-92BK-WNPC"})
     == {"backend": "cloud", "joinCode": "H4RT-92BK-WNPC", "apiBase": "", "hubId": "", "visibility": ""})
case("...and one with nothing as unbound", server_app._stacking_of({})["backend"] == ""
     and server_app._stacking_of({"stackingJoinCode": ""})["backend"] == "")
listing = project_in_listing(bobs_token := call("POST", "/api/v1/agents", {"name": "Bob bind rig"}, token=BOBH)[1]["token"], CLOUD)
case("the listing keeps stackingLinked and stackingJoinCode and says cloud in the block",
     listing["stackingLinked"] is True and listing["stackingJoinCode"] == "H4RT-92BK-WNPC"
     and listing["stacking"]["backend"] == "cloud" and listing["stacking"]["grant"] == ""
     and listing["stacking"]["collabProjectId"] == "", str(listing["stacking"]))

# Bind to a hub: as its owner, then as a listed coordinator, then as someone else.
status, made = call("POST", "/api/v1/projects", {
    "name": "Orion Hub Wide!", "region": REGION, "notes": "hub stacked",
    "requirements": {"filters": {"Ha": 3.0, "OIII": 3.0}},
    "stacking": {"backend": "hub", "hubId": BH}}, token=BOBH)
HUBP = made["project"]["id"]
row = server_app.store.stack_project_of(HUBP) or {}
doc = row.get("doc") or {}
case("the hub's owner binds a collaboration to it", status == 200 and row, str(made)[:200])
case("...the stacking project id is prj_ + 20 lower-case Crockford characters",
     _re.fullmatch(r"prj_[0-9a-hjkmnp-tv-z]{20}", row.get("project_id", "")) is not None,
     row.get("project_id", ""))
case("...the slug is made from the name and fits the contract",
     row.get("slug") == "orion-hub-wide" and _re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", row["slug"]),
     row.get("slug", ""))
case("...the join code is twelve characters as XXXX-XXXX-XXXX and a real one",
     _re.fullmatch(r"[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", row.get("join_code", "")) is not None
     and server_app._stacking_join_code(row["join_code"]) == row["join_code"], row.get("join_code", ""))
case("...unlisted by default, open, approval open, bound to the hub",
     doc.get("visibility") == "unlisted" and doc.get("status") == "open"
     and doc.get("approval") == "open" and row.get("hub") == BH, str(doc)[:200])
case("...the document is a Project record: ids, filters, palette, grid, policy, licence",
     doc.get("project_id") == row["project_id"] and doc.get("slug") == row["slug"]
     and doc.get("name") == "Orion Hub Wide!" and doc.get("filters") == ["HA", "OIII"]
     and doc.get("palette") == "HOO" and doc.get("licence") == "members_only"
     and doc.get("rotation_from_first_light") is True
     and doc.get("policy") == stack_defaults.POLICY, str(doc)[:300])
grid = doc.get("grid") or {}
case("...the grid is the region's centre and size in the contract's limits",
     grid.get("center_ra_deg") == 84.0 and grid.get("center_dec_deg") == 0.0
     and 64 <= grid.get("width_px", 0) <= 12000 and 64 <= grid.get("height_px", 0) <= 12000
     and 0.1 <= grid.get("scale_arcsec_px", 0) <= 20
     and abs(grid["width_px"] * grid["scale_arcsec_px"] / 3600.0 - 16.0) < 0.05
     and abs(grid["height_px"] * grid["scale_arcsec_px"] / 3600.0 - 8.0) < 0.05, str(grid))
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since={SINCE}&wait=0", token=BHTOK)
case("...and a project change for it is in the hub's feed",
     [(c["kind"], c["key"]) for c in pulled["changes"]] == [("project", row["project_id"])],
     str(pulled["changes"]))
SINCE = pulled["seq"]
status, second = call("POST", "/api/v1/projects", {
    "name": "Orion Hub Wide!", "region": REGION,
    "stacking": {"backend": "hub", "hubId": BH, "visibility": "public"}}, token=BOBH)
other_row = server_app.store.stack_project_of(second["project"]["id"]) or {}
case("a second collaboration of the same name gets its own unique slug and join code",
     status == 200 and other_row.get("slug") == "orion-hub-wide-2"
     and other_row["join_code"] != row["join_code"]
     and other_row["doc"]["visibility"] == "public")

status, made = call("POST", "/api/v1/projects", {
    "name": "Coordinator's", "region": REGION, "requirements": {"filters": {"Ha": 3.0}},
    "stacking": {"backend": "hub", "hubId": BH}}, token=CAROL)
COORD = made["project"]["id"]
case("a coordinator the owner listed may bind to the hub too",
     status == 200 and (server_app.store.stack_project_of(COORD) or {}).get("hub") == BH, str(made)[:160])
status, answer = call("POST", "/api/v1/projects", {
    "name": "Not allowed", "region": REGION, "stacking": {"backend": "hub", "hubId": BH}},
    token=ALICE)
case("anybody else is refused, as 403, and nothing is made",
     status == 403 and server_app.store._one("SELECT 1 AS x FROM projects WHERE name = ?", "Not allowed") is None,
     f"{status} {answer}")
status, answer = call("POST", "/api/v1/projects", {
    "name": "No such hub", "region": REGION, "stacking": {"backend": "hub", "hubId": "hub_nope"}},
    token=BOBH)
case("...and so is a hub that does not exist", status == 403)
status, _ = call("POST", "/api/v1/projects", {
    "name": "No hub id", "region": REGION, "stacking": {"backend": "hub"}}, token=BOBH)
case("...or a hub binding that names none", status == 403)
call("PUT", f"/api/v1/hubs/{BH}/coordinators", {"coordinators": []}, token=BOBH)
status, _ = call("PUT", f"/api/v1/projects/{CLOUD}", {"stacking": {"backend": "hub", "hubId": BH}},
                 token=BOBH)
case("an edit may move a collaboration onto a hub its owner owns", status == 200
     and (server_app.store.stack_project_of(CLOUD) or {}).get("hub") == BH)
call("PUT", f"/api/v1/hubs/{BH}/coordinators", {"coordinators": ["1004"]}, token=BOBH)
payload = server_app.store.project(CLOUD)["payload"]
case("...which clears the cloud join code, both ways",
     payload["stackingJoinCode"] == "" and payload["stacking"]["backend"] == "hub")

# The listing: who is handed the grant.
OWNER_RIG = call("POST", "/api/v1/agents", {"name": "Bob's hub rig"}, token=BOBH)[1]["token"]
JOINED_RIG, JOINED_ID = enrol("hub joiner", 389.0, 9576, 6388, 6.0)
status, _ = call("POST", f"/api/v1/agent/projects/{HUBP}/join", {}, token=JOINED_RIG)
case("a telescope joins the hub-backed collaboration", status == 200)
wanted_id = row["project_id"]
mine_l = project_in_listing(OWNER_RIG, HUBP)["stacking"]
joined_l = project_in_listing(JOINED_RIG, HUBP)["stacking"]
third_l = project_in_listing(AGENT_TOKEN, HUBP)["stacking"]
case("the listing's block names the hub and where its front door is",
     mine_l["backend"] == "hub" and mine_l["hubId"] == BH and mine_l["hubName"] == "Bind hub"
     and mine_l["hubLanUrl"] == "http://10.0.0.7:8790" and mine_l["collabProjectId"] == wanted_id
     and mine_l["apiBase"] == BASE + "/stack/api/", str(mine_l))
case("...the owner's own telescope is handed a grant without joining", mine_l["grant"].startswith("sfg_"))
case("...a joined telescope is handed one", joined_l["grant"].startswith("sfg_"))
case("...a third telescope, only browsing, gets the block and an empty grant",
     third_l["backend"] == "hub" and third_l["collabProjectId"] == wanted_id and third_l["grant"] == "",
     str(third_l))
claims = hubs_module.verify_grant(mine_l["grant"], BHSEC)
case("the owner's grant verifies with the hub's secret and names the project and the person",
     claims and claims["hub"] == BH and claims["project"] == wanted_id
     and claims["sub"] == "1002" and claims["name"] == "Bob", str(claims))
claims = hubs_module.verify_grant(joined_l["grant"], BHSEC)
case("...a joined one names its telescope and the project",
     claims and claims["agent"] == JOINED_ID and claims["project"] == wanted_id
     and claims["sub"] == JOINED_ID, str(claims))
case("...and neither verifies under another hub's secret",
     hubs_module.verify_grant(mine_l["grant"], reg_b["grantSecret"]) is None
     and hubs_module.verify_grant(joined_l["grant"], reg_b["grantSecret"]) is None)
old_view = project_in_listing(OWNER_RIG, HUBP)
case("the old fields are as today: a hub project carries no join code for old plugins",
     old_view["stackingLinked"] is False and old_view["stackingJoinCode"] == "")

# Editing keeps the document in step; an old-form edit leaves a hub binding alone.
status, _ = call("PUT", f"/api/v1/projects/{HUBP}", {"stackingJoinCode": ""}, token=BOBH)
case("a blank stackingJoinCode from an old plugin does not unbind a hub project",
     status == 200 and server_app._stacking_of(server_app.store.project(HUBP)["payload"])["backend"] == "hub")
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since={SINCE}&wait=0", token=BHTOK)
SINCE = pulled["seq"]
status, _ = call("PUT", f"/api/v1/projects/{HUBP}", {"name": "Orion Renamed"}, token=BOBH)
doc = server_app.store.stack_project_of(HUBP)["doc"]
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since={SINCE}&wait=0", token=BHTOK)
case("renaming a collaboration renames the stacking project and tells the hub",
     doc["name"] == "Orion Renamed" and doc["slug"] == "orion-hub-wide"
     and any(c["key"] == wanted_id for c in pulled["changes"]), str(pulled["changes"]))
SINCE = pulled["seq"]

# The backend cannot change once a frame exists.
server_app.store._run("INSERT INTO stack_frames (frame_id, project_id) VALUES ('frm_t403', ?)", wanted_id)
status, answer = call("PUT", f"/api/v1/projects/{HUBP}", {"stacking": {"backend": ""}}, token=BOBH)
case("with a frame stacked, unbinding is refused as a conflict", status == 409, f"{status} {answer}")
status, answer = call("PUT", f"/api/v1/projects/{HUBP}", {"stacking": {
    "backend": "cloud", "joinCode": "H4RT-92BK-WNPC"}}, token=BOBH)
case("...and so is moving it to the cloud", status == 409)
status, _ = call("PUT", f"/api/v1/projects/{HUBP}", {"stacking": {"backend": "hub", "hubId": BH},
                                                     "notes": "still here"}, token=BOBH)
case("...while an edit that keeps the binding goes through", status == 200)
server_app.store._run("DELETE FROM stack_frames WHERE frame_id = 'frm_t403'")

# Unbinding closes, never deletes.
status, _ = call("PUT", f"/api/v1/projects/{HUBP}", {"stacking": {"backend": ""}}, token=BOBH)
row = server_app.store.stack_project_of(HUBP) or {}
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since={SINCE}&wait=0", token=BHTOK)
case("unbinding marks the stacking project closed, keeps the row, and tells the hub",
     status == 200 and row.get("doc", {}).get("status") == "closed"
     and any(c["key"] == wanted_id for c in pulled["changes"]), str(row.get("doc", {}).get("status")))
listing = project_in_listing(OWNER_RIG, HUBP)
case("...and the listing no longer stacks it or hands out a grant",
     listing["stacking"]["backend"] == "" and listing["stacking"]["grant"] == ""
     and listing["stackingLinked"] is False)
status, _ = call("PUT", f"/api/v1/projects/{HUBP}", {"stacking": {"backend": "hub", "hubId": BH}}, token=BOBH)
again = server_app.store.stack_project_of(HUBP)
case("binding it again reopens the same stacking project",
     status == 200 and again["project_id"] == wanted_id and again["join_code"] == row["join_code"]
     and again["doc"]["status"] == "open")

# The policy defaults are the contract's own, when the contract is at hand.
spec = None
for candidate in (ROOT.parent / "Rust" / "rapid-stacker-collab", ROOT.parent.parent / "Rust" / "rapid-stacker-collab",
                  ROOT.parent / "rapid-stacker-collab", ROOT / "nina" / "docs" / "api" / ".."):
    found = candidate / "docs" / "api" / "openapi.yaml" if candidate.name != ".." else candidate / "openapi.yaml"
    if found.is_file():
        spec = found
        break
try:
    import yaml as _yaml
except ImportError:
    _yaml = None
if spec is None or _yaml is None:
    print("SKIP  policy defaults against openapi.yaml: "
          + ("PyYAML is not installed" if spec else "no openapi.yaml beside this repository"))
else:
    schemas = _yaml.safe_load(spec.read_text(encoding="utf-8"))["components"]["schemas"]
    contract = {key: value["default"] for key, value in schemas["Policy"]["properties"].items()
                if "default" in value}
    case("server/stack/defaults.py POLICY equals the contract's Policy defaults",
         contract == stack_defaults.POLICY,
         str({k: (contract.get(k), stack_defaults.POLICY.get(k))
              for k in set(contract) | set(stack_defaults.POLICY)
              if contract.get(k) != stack_defaults.POLICY.get(k)}))
    filters_enum = tuple(schemas["CanonicalFilter"]["enum"])
    case("...and CANONICAL_FILTERS equals the contract's CanonicalFilter",
         filters_enum == stack_defaults.CANONICAL_FILTERS, str(filters_enum))

# ------------------------------------------- live stacking: front door
# The hub-backed project's door, in the Collab contract's envelope: grants for
# auth, projects, membership, rigs, and no 403/404 anywhere under /stack/api.
print("\n-- live stacking: front door --")
import subprocess as _sp                                          # noqa: E402
import uuid as _uuid                                              # noqa: E402
from server.stack import envelope as stack_envelope               # noqa: E402
from server.stack import header as stack_header                   # noqa: E402
from server.stack import rigs as stack_rigs                       # noqa: E402

API = "/stack/api"
OWNER_GRANT = mine_l["grant"]
JOINED_GRANT = joined_l["grant"]
SECOND_ID = second["project"]["id"]
SECOND_GRANT = project_in_listing(OWNER_RIG, SECOND_ID)["stacking"]["grant"]
FD = server_app.store.stack_project_of(HUBP)
FD_ID, FD_SLUG, FD_CODE = FD["project_id"], FD["slug"], FD["join_code"]
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since=0&wait=0", token=BHTOK)
FEED0 = pulled["seq"]

# What the contract says, when it is at hand.
rsc = None
for candidate in (ROOT.parent / "Rust" / "rapid-stacker-collab", ROOT.parent.parent / "Rust" / "rapid-stacker-collab",
                  ROOT.parent / "rapid-stacker-collab"):
    if (candidate / "docs" / "api" / "openapi.yaml").exists():
        rsc = candidate
        break
schemas = {}
if rsc is not None and _yaml is not None:
    contract_doc = _yaml.safe_load((rsc / "docs" / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = contract_doc["components"]["schemas"]
else:
    print("SKIP  front door against openapi.yaml: "
          + ("PyYAML is not installed" if rsc else "no Rapid Stacker Collab beside this repository"))


def resolve_ref(node):
    while isinstance(node, dict) and "$ref" in node:
        node = schemas[node["$ref"].rsplit("/", 1)[1]]
    return node


def problems(value, node, path="$"):
    """A small required/type/enum/const/properties/additionalProperties check."""
    node = resolve_ref(node)
    found = []
    if "oneOf" in node:
        if not any(not problems(value, option, path) for option in node["oneOf"]):
            found.append(f"{path}: matches none of oneOf")
        return found
    kinds = node.get("type")
    kinds = [kinds] if isinstance(kinds, str) else (kinds or [])
    pytypes = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
               "array": list, "object": dict, "null": type(None)}
    if kinds and not any(isinstance(value, pytypes[k]) and not (k in ("integer", "number") and isinstance(value, bool))
                         for k in kinds):
        return [f"{path}: {type(value).__name__} is not {kinds}"]
    if "const" in node and value != node["const"]:
        found.append(f"{path}: not {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        found.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str) and "pattern" in node and not _re.search(node["pattern"], value):
        found.append(f"{path}: {value!r} fails pattern")
    if isinstance(value, dict):
        props = node.get("properties", {})
        for key in node.get("required", []):
            if key not in value:
                found.append(f"{path}.{key}: missing")
        for key, item in value.items():
            if key in props:
                found += problems(item, props[key], f"{path}.{key}")
            elif node.get("additionalProperties") is False:
                found.append(f"{path}.{key}: not in the schema")
    if isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            found += problems(item, node["items"], f"{path}[{i}]")
    return found


def conforms(name, body):
    return (not schemas) or not problems(body, {"$ref": f"#/components/schemas/{name}"})


def why(name, body):
    return "; ".join(problems(body, {"$ref": f"#/components/schemas/{name}"}))[:300] if schemas else ""


def front(method, path, body=None, token=None):
    return call(method, API + path, body, token=token)


# -- the envelope table and the allow-list against the contract
if schemas:
    import re as _re2
    text = schemas["ErrorCode"]["description"]
    table = {m.group(1): int(m.group(2)) for m in _re2.finditer(r"- `(\w+)` \((\d+)\)", text)}
    case("envelope.ERROR_STATUS equals the contract's ErrorCode table",
         table == stack_envelope.ERROR_STATUS,
         str({k for k in set(table) | set(stack_envelope.ERROR_STATUS)
              if table.get(k) != stack_envelope.ERROR_STATUS.get(k)}))
    case("...and no code of the table is a 403 or 404",
         not {403, 404} & set(stack_envelope.ERROR_STATUS.values()))
    initheader = schemas["InitHeader"]["properties"]
    kind_of = {"string": "str", "number": "num", "integer": "int"}
    same = set(initheader) == set(stack_header.ALLOWED) and all(
        stack_header.ALLOWED[k][0] == ("numstr" if isinstance(v["type"], list) else kind_of[v["type"]])
        for k, v in initheader.items())
    case("header.ALLOWED equals the contract's InitHeader allow-list", same,
         str(set(initheader) ^ set(stack_header.ALLOWED)))

# -- header allow-list
case("an allowed header passes", stack_header.check({"FILTER": "Ha", "EXPTIME": 300.0, "XBINNING": 1,
                                                    "RA": "05 35 17", "DEC": -5.4}) is not None)
for card in ("SITELAT", "SITELONG", "SITEELEV", "OBSERVER", "HISTORY"):
    try:
        stack_header.check({"FILTER": "Ha", card: "x"})
        refused = ""
    except stack_envelope.ApiError as exc:
        refused = f"{exc.code} {exc.status} {exc.extra['errors'][0]['field']}"
    case(f"header: {card} is refused with header_rejected", refused == f"header_rejected 400 header.{card}", refused)
case("header: a location card is never kept from a file's header",
     stack_header.without_location({"SITELAT": 1, "OBSERVER": "me", "FILTER": "Ha"}) == {"FILTER": "Ha"})
try:
    stack_header.check({"EXPTIME": "long"})
    wrong = ""
except stack_envelope.ApiError as exc:
    wrong = exc.code
case("header: a value of the wrong type is validation_failed", wrong == "validation_failed", wrong)

# -- authentication
status, got = front("GET", "/rigs")
case("no credential: 401 not_authenticated in the envelope",
     status == 401 and got.get("ok") is False and got.get("code") == "not_authenticated", f"{status} {got}")
tampered = OWNER_GRANT[:-3] + ("AAA" if OWNER_GRANT[-3:] != "AAA" else "BBB")
status, got = front("GET", f"/projects/{FD_ID}/membership", token=tampered)
case("a tampered grant: 401 token_invalid", status == 401 and got.get("code") == "token_invalid", f"{status} {got}")
status, got = front("GET", "/rigs", token="rscu_" + "0" * 64)
case("a token of another kind: 401 token_invalid", status == 401 and got.get("code") == "token_invalid", f"{status} {got}")
other_secret = hubs_module.mint_grant({"id": BH, "grant_secret": reg_b["grantSecret"]}, "1002", "Bob", "x", FD_ID)
status, got = front("GET", "/rigs", token=other_secret)
case("a grant signed with another hub's secret: 401 token_invalid",
     status == 401 and got.get("code") == "token_invalid", f"{status} {got}")
old_grant = hubs_module.mint_grant({"id": BH, "grant_secret": BHSEC}, "1002", "Bob", "x", FD_ID, time.time() - 3 * 86400)
status, got = front("GET", "/rigs", token=old_grant)
case("an expired grant: 401 token_invalid", status == 401 and got.get("code") == "token_invalid", f"{status} {got}")
status, got = front("GET", f"/projects/{FD_ID}/membership", token=SECOND_GRANT)
case("a grant for another project: 401 token_scope",
     status == 401 and got.get("code") == "token_scope", f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", {"display_name": "Zed", "accepted_terms_version": "x"}, token=SECOND_GRANT)
case("...on a write too", status == 401 and got.get("code") == "token_scope", f"{status} {got}")

# -- the envelope for what is not there
status, got = front("GET", "/nope")
case("an unknown path answers the envelope, not 403 or 404",
     status not in (403, 404) and got.get("ok") is False and got.get("code") in stack_envelope.ERROR_STATUS
     and status == stack_envelope.ERROR_STATUS[got["code"]], f"{status} {got}")
for path in ("/auth/me", f"/projects/{FD_ID}/events", f"/projects/{FD_ID}/masters", f"/projects/{FD_ID}/mosaic"):
    status, got = front("GET", path)
    case(f"{path.rsplit('/', 1)[-1]} (not built yet) is still the envelope",
         status not in (403, 404) and got.get("ok") is False and "code" in got, f"{status} {got}")
status, got = front("DELETE", "/rigs")
case("a method not allowed is the envelope too", status not in (403, 404, 405) and got.get("ok") is False, f"{status} {got}")

# -- resolve-join-code
status, got = front("POST", "/projects/resolve-join-code", {"code": FD_CODE.lower().replace("-", " ")}, token=OWNER_GRANT)
case("a grant resolves the join code, in any case and spacing",
     status == 200 and got.get("ok") is True and got["project"]["project_id"] == FD_ID
     and got["terms_version"] and got["already_member"] is False, f"{status} {str(got)[:200]}")
case("...the response validates against ResolveJoinCodeResponse", conforms("ResolveJoinCodeResponse", got), why("ResolveJoinCodeResponse", got))
status, got = front("POST", "/projects/resolve-join-code", {"code": "H4RT-92BK-WNPC"}, token=OWNER_GRANT)
case("an unknown code: 400 invalid_code", status == 400 and got.get("code") == "invalid_code", f"{status} {got}")
status, got = front("POST", "/projects/resolve-join-code", {}, token=OWNER_GRANT)
case("a missing code: 400 invalid_request", status == 400 and got.get("code") == "invalid_request", f"{status} {got}")
status, got, _ = raw("POST", API + "/projects/resolve-join-code", data=b"{not json",
                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {OWNER_GRANT}"})
case("a body that is not JSON: 400 invalid_request", status == 400 and got.get("code") == "invalid_request", f"{status} {got}")

# -- projects, by id and by slug
status, by_id = front("GET", f"/projects/{FD_ID}", token=OWNER_GRANT)
status2, by_slug = front("GET", f"/projects/{FD_SLUG}", token=OWNER_GRANT)
case("the project reads by id and by slug", status == status2 == 200
     and by_id["project"]["project_id"] == FD_ID == by_slug["project"]["project_id"]
     and by_id["project"]["slug"] == FD_SLUG, f"{status} {status2}")
case("...and validates against ProjectResponse", conforms("ProjectResponse", by_id), why("ProjectResponse", by_id))
case("...with the placeholder grid, the filters and no membership yet",
     by_id["project"]["grid"]["width_px"] >= 64 and by_id["project"]["filters"] == ["HA", "OIII"]
     and by_id["project"]["my_membership"] is None and by_id["project"]["owner_name"] == "Bob")
status, got = front("GET", "/projects/prj_zzzzzzzzzzzzzzzzzzzz", token=OWNER_GRANT)
case("an unknown project: 400 not_found", status == 400 and got.get("code") == "not_found", f"{status} {got}")
status, got = front("GET", f"/projects/{FD_SLUG}")
case("an unlisted project reads without a credential", status == 200 and got["project"]["project_id"] == FD_ID, f"{status} {got}")

# -- joining
body = {"code": "", "display_name": "Bobby", "accepted_terms_version": "2020-01-01",
        "filter_mapping": [{"raw": "Ha 7nm", "canonical": "HA"}]}
status, got = front("POST", f"/projects/{FD_ID}/join", body, token=OWNER_GRANT)
case("joining on stale terms: 409 terms_outdated with the current version",
     status == 409 and got.get("code") == "terms_outdated" and got.get("terms_version"), f"{status} {got}")
body["accepted_terms_version"] = by_id["project"]["terms_version"]
status, got = front("POST", f"/projects/{FD_ID}/join", dict(body, code="WNPC-WNPC-WNPC"), token=OWNER_GRANT)
case("a non-empty code that is not the project's: 400 invalid_code",
     status == 400 and got.get("code") == "invalid_code", f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", dict(body, filter_mapping=[{"raw": "x", "canonical": "NOPE"}]), token=OWNER_GRANT)
case("a filter mapping outside the canonical filters: 400 validation_failed",
     status == 400 and got.get("code") == "validation_failed" and got.get("errors"), f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", {"display_name": "Bobby"}, token=OWNER_GRANT)
case("a body missing the terms version: 400 invalid_request", status == 400 and got.get("code") == "invalid_request", f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", body, token=OWNER_GRANT)
member = got.get("membership") or {}
case("an empty code joins on a grant for the project, active under approval open",
     status == 200 and member.get("status") == "active" and member.get("role") == "contributor"
     and member.get("display_name") == "Bobby" and member.get("filter_mapping") == body["filter_mapping"], f"{status} {str(got)[:200]}")
case("...the response validates against JoinResponse", conforms("JoinResponse", got), why("JoinResponse", got))
OWNER_UID = member.get("user_id", "")
case("...the user id is a UUID v4 that is not the Starfront id",
     _re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", OWNER_UID) is not None
     and OWNER_UID != "1002")
case("...recorded in stack_identities, once",
     (server_app.store._one("SELECT collab_user_id FROM stack_identities WHERE user_id = '1002'") or {}).get("collab_user_id") == OWNER_UID)
status, got = front("POST", f"/projects/{FD_ID}/join", body, token=OWNER_GRANT)
case("joining twice: 409 already_member", status == 409 and got.get("code") == "already_member", f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", dict(body, display_name="  BOBBY ", code=FD_CODE.lower()), token=JOINED_GRANT)
case("a second person with the same display name (any case): 409 display_name_taken",
     status == 409 and got.get("code") == "display_name_taken", f"{status} {got}")
status, got = front("POST", f"/projects/{FD_ID}/join", dict(body, display_name="Joiner", code=FD_CODE.lower()), token=JOINED_GRANT)
case("...who joins under another name with the real code (lower case)",
     status == 200 and got["membership"]["display_name"] == "Joiner"
     and got["membership"]["user_id"] != OWNER_UID, f"{status} {got}")
JOINER_UID = got["membership"]["user_id"]

# -- membership
status, got = front("GET", f"/projects/{FD_ID}/membership", token=OWNER_GRANT)
case("the membership reads back", status == 200 and got["membership"]["user_id"] == OWNER_UID
     and got["membership"]["usage"]["frames_used"] == 0 and conforms("MembershipResponse", got), f"{status} {why('MembershipResponse', got)}")
status, got = front("PUT", f"/projects/{FD_ID}/membership", {"display_name": "Robert", "country": "GB",
                    "allow_owner_raw_download": True,
                    "filter_mapping": [{"raw": "Oiii", "canonical": "OIII"}, {"raw": "Dark", "canonical": "SKIP"}]}, token=OWNER_GRANT)
case("...updates (name, country, opt-in, mapping)", status == 200 and got["membership"]["display_name"] == "Robert"
     and got["membership"]["country"] == "GB" and got["membership"]["allow_owner_raw_download"] is True
     and len(got["membership"]["filter_mapping"]) == 2 and conforms("MembershipResponse", got), f"{status} {str(got)[:200]}")
status, got = front("PUT", f"/projects/{FD_ID}/membership", {"display_name": "joiner"}, token=OWNER_GRANT)
case("...and a name another member has is 409 display_name_taken", status == 409 and got.get("code") == "display_name_taken", f"{status} {got}")
status, got = front("PUT", f"/projects/{FD_ID}/membership", {"display_name": 5}, token=OWNER_GRANT)
case("...and a bad body is 400 invalid_request", status == 400 and got.get("code") == "invalid_request", f"{status} {got}")
status, got = front("GET", f"/projects/{FD_ID}", token=OWNER_GRANT)
case("the project now shows the caller's membership and the member count",
     got["project"]["my_membership"]["display_name"] == "Robert" and got["project"]["counts"]["member_count"] == 2
     and conforms("ProjectResponse", got), why("ProjectResponse", got))
NOBODY = hubs_module.mint_grant({"id": BH, "grant_secret": BHSEC}, "9999", "Nobody", "x", FD_ID)
status, got = front("GET", f"/projects/{FD_ID}/membership", token=NOBODY)
case("a grant for the project whose holder has not joined: 401 not_member",
     status == 401 and got.get("code") == "not_member", f"{status} {got}")

# -- a browser session reads, and cannot write
server_app.store.add_web_token("web-front-door", "1002")
request = urllib.request.Request(f"{BASE}{API}/projects/{FD_ID}/membership", headers={"Cookie": "starfront_web=web-front-door"})
with urllib.request.urlopen(request, timeout=10) as response:
    cookie_read = json.loads(response.read())
case("the starfront_web cookie reads the same membership", cookie_read["membership"]["user_id"] == OWNER_UID, str(cookie_read)[:200])
status, got, _ = raw("PUT", API + f"/projects/{FD_ID}/membership", data=b'{"country": "FR"}',
                     headers={"Content-Type": "application/json", "Cookie": "starfront_web=web-front-door"})
case("...but a write on the cookie alone is refused in the envelope", status == 401 and got.get("code") == "not_authenticated", f"{status} {got}")
status, got, _ = raw("GET", API + "/rigs", headers={"Cookie": "starfront_web=unknown"})
case("...and an unknown session is 401 session_expired", status == 401 and got.get("code") == "session_expired", f"{status} {got}")

# -- rigs
RIG = {"camera": "ASI2600MM Pro ", "telescope": "RedCat 51", "focal_length_mm": 250.4,
       "pixel_size_um": 3.76, "binning": 1, "name": "Wide rig"}
status, got = front("POST", "/rigs", RIG, token=OWNER_GRANT)
rig = got.get("rig") or {}
case("a rig is upserted, confirmed, with its pixel scale", status == 200 and rig.get("confirmed") is True
     and rig.get("osc") is False and rig.get("name") == "Wide rig" and rig.get("pixel_scale_arcsec") == 3.0973
     and conforms("RigResponse", got), f"{status} {why('RigResponse', got)} {rig}")
want = stack_rigs.rig_id_of(stack_rigs.fingerprint("asi2600mm pro", "redcat 51", 250, 3.8, 1, False))
case("...its rig_id is the fingerprint's first 12 hex characters", rig.get("rig_id") == want, f"{rig.get('rig_id')} {want}")
# The fingerprint against Rapid Stacker Collab's own code, when node and the file are at hand.
js = rsc / "backend" / "lambdas" / "shared" / "rigs.js" if rsc else None
node_ok = False
if js is not None and js.exists():
    probe = ("const ids=require('" + str(js.parent / "ids.js") + "');"
             "const src=require('fs').readFileSync('" + str(js) + "','utf8');"
             "const m={exports:{}};const crypto=require('crypto');"
             "const fn=new Function('require','module','exports',src.replace(/require\\(\"\\.\\/ddb\"\\)/,'{}'));"
             "fn(p=>p==='./ids'?ids:require(p),m,m.exports);"
             "const cases=JSON.parse(process.argv[1]);"
             "console.log(JSON.stringify(cases.map(c=>m.exports.fingerprint(c))));")
    vectors = [dict(camera="ASI2600MM Pro ", telescope="RedCat 51", focal_length_mm=250.4, pixel_size_um=3.76, binning=1, osc=False),
               dict(camera="IMX571", telescope=" Esprit 100", focal_length_mm=550.5, pixel_size_um=3.75, binning=2, osc=True),
               dict(camera="QHY268C", telescope="X", focal_length_mm=1000, pixel_size_um=2.25, binning=1, osc=True),
               dict(camera="A", telescope="B", focal_length_mm=0.5, pixel_size_um=4.65, binning=3, osc=False)]
    try:
        out = _sp.run(["node", "-e", probe, json.dumps(vectors)], capture_output=True, text=True, timeout=30,
                      env={**os.environ, "NODE_PATH": str(rsc / "backend" / "node_modules")})
        theirs = json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, _sp.TimeoutExpired):
        theirs = None
    if theirs is None:
        print("SKIP  rig fingerprint against rigs.js: it could not be run here")
    else:
        mine_fp = [stack_rigs.fingerprint(v["camera"], v["telescope"], v["focal_length_mm"], v["pixel_size_um"],
                                          v["binning"], v["osc"]) for v in vectors]
        case("the rig fingerprint equals Rapid Stacker Collab's rigs.js on four vectors", mine_fp == theirs, f"{mine_fp} {theirs}")
else:
    print("SKIP  rig fingerprint against rigs.js: no Rapid Stacker Collab beside this repository")
status, again = front("POST", "/rigs", dict(RIG, camera="asi2600mm pro", name="Renamed"), token=OWNER_GRANT)
case("the same tuple upserts the same rig (idempotent, case-insensitive), renaming it",
     again["rig"]["rig_id"] == rig["rig_id"] and again["rig"]["name"] == "Renamed"
     and again["rig"]["created_at"] == rig["created_at"], str(again)[:200])
status, got = front("POST", "/rigs", dict(RIG, bayer_pattern="RGGB", width_px=6248, height_px=4176), token=OWNER_GRANT)
case("a bayer pattern makes it a different, OSC rig", got["rig"]["osc"] is True and got["rig"]["rig_id"] != rig["rig_id"]
     and got["rig"]["width_px"] == 6248 and conforms("RigResponse", got), f"{status} {why('RigResponse', got)}")
status, got = front("POST", "/rigs", dict(RIG, binning=0), token=OWNER_GRANT)
case("a bad rig body is validation_failed", status == 400 and got.get("code") == "validation_failed", f"{status} {got}")
status, got = front("POST", "/rigs", {"camera": "x"}, token=OWNER_GRANT)
case("...or invalid_request when a field is missing", status == 400 and got.get("code") == "invalid_request", f"{status} {got}")
status, got = front("GET", "/rigs", token=OWNER_GRANT)
case("rigs list, oldest first, validating against RigListResponse",
     status == 200 and len(got["rigs"]) == 2 and got["rigs"][0]["rig_id"] == rig["rig_id"]
     and conforms("RigListResponse", got), f"{status} {why('RigListResponse', got)}")
status, got = front("GET", "/rigs", token=JOINED_GRANT)
case("...and are the caller's own", status == 200 and got["rigs"] == [])
status, got = front("PATCH", f"/rigs/{rig['rig_id']}", {"name": "Final", "confirmed": False}, token=OWNER_GRANT)
case("a rig is patched (name, confirmed)", status == 200 and got["rig"]["name"] == "Final"
     and got["rig"]["confirmed"] is False and conforms("RigResponse", got), f"{status} {got}")
status, got = front("PATCH", f"/rigs/{rig['rig_id']}", {"name": "  "}, token=OWNER_GRANT)
case("...a blank name is validation_failed", status == 400 and got.get("code") == "validation_failed", f"{status} {got}")
status, got = front("PATCH", "/rigs/rig_000000000000", {"name": "x"}, token=OWNER_GRANT)
case("...a rig that is not yours is 400 not_found", status == 400 and got.get("code") == "not_found", f"{status} {got}")
status, got = front("PATCH", f"/rigs/{rig['rig_id']}", {"name": "Hijack"}, token=JOINED_GRANT)
case("...another person cannot patch it", status == 400 and got.get("code") == "not_found", f"{status} {got}")

# -- the hub hears of it, and never learns a Starfront id
_, pulled = call("GET", f"/api/v1/hubs/{BH}/pull?since={FEED0}&wait=0", token=BHTOK)
members = [c["key"] for c in pulled["changes"] if c["kind"] == "member"]
rigs_seen = [c["key"] for c in pulled["changes"] if c["kind"] == "rig"]
case("each membership write is a member row in the feed, key project_id/user_id",
     members.count(f"{FD_ID}/{OWNER_UID}") == 2 and members.count(f"{FD_ID}/{JOINER_UID}") == 1, str(members))
case("each rig write is a rig row, key user_id/rig_id",
     rigs_seen.count(f"{OWNER_UID}/{rig['rig_id']}") >= 3 and all(k.split("/")[0] in (OWNER_UID,) for k in rigs_seen), str(rigs_seen))
everything = json.dumps([by_id, member, cookie_read, pulled]) + json.dumps(
    [server_app.store._all("SELECT * FROM stack_members"), server_app.store._all("SELECT * FROM stack_rigs")])
case("no Starfront or Discord id appears in a response, a member row, a rig row or the feed",
     not any(f'"{sid}"' in everything or f"/{sid}\"" in everything or f'{sid}/' in everything
             for sid in ("1002", JOINED_ID)), "")

# ------------------------------------------- live stacking: the schema
print("\n-- live stacking: hub and stacking tables --")
import sqlite3                                                   # noqa: E402
from server import store as store_module                         # noqa: E402

STACK_TABLES = {"hubs", "stack_projects", "stack_members", "stack_rigs",
                "stack_frames", "stack_parts", "stack_library", "stack_changes",
                "stack_identities", "stack_notified"}


def tables_of(path):
    link = sqlite3.connect(str(path))
    try:
        return {row[0] for row in link.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        link.close()


fresh_path = Path(tempfile.mkdtemp()) / "fresh.db"
fresh = store_module.Store(fresh_path)
case("a fresh database has every hub and stacking table",
     STACK_TABLES <= tables_of(fresh_path), str(STACK_TABLES - tables_of(fresh_path)))

# A database from before this change: today's schema without the new tables,
# holding a row that must survive being opened by the new code.
old_path = Path(tempfile.mkdtemp()) / "old.db"
link = sqlite3.connect(str(old_path))
link.executescript(store_module.SCHEMA.split("-- Live stacking")[0])
link.execute("INSERT INTO agents (id, token, name, created) VALUES"
             " ('a1', 't1', 'Old rig', 1)")
link.commit()
case("the test's pre-change database really lacks the new tables",
     not (STACK_TABLES & tables_of(old_path)))
link.close()
old = store_module.Store(old_path)
case("opening a pre-change database adds the tables",
     STACK_TABLES <= tables_of(old_path))
case("...and keeps what was in it", (old.agent("a1") or {}).get("name") == "Old rig")
old.close()
again = store_module.Store(old_path)
case("opening it a second time is harmless", (again.agent("a1") or {}).get("name") == "Old rig")
again.close()

made_hub = fresh.add_hub("hub_1", "u1", "Eddie's hub", "sfh_tok", "secret", "http://10.0.0.5:8790")
case("a hub can be stored and read back",
     made_hub["name"] == "Eddie's hub" and made_hub["status"] == {}
     and fresh.hub("hub_1")["grant_secret"] == "secret" and fresh.hub("nope") is None)
seqs = [fresh.append_change("hub_1", "project", "prj_a"),
        fresh.append_change("hub_1", "member", "prj_a/u1"),
        fresh.append_change("hub_2", "project", "prj_b")]
case("stack_changes.seq only ever increases", seqs == sorted(set(seqs)) and seqs[0] >= 1, str(seqs))
fresh._run("DELETE FROM stack_changes")
case("...and is not reused after the rows are pruned", fresh.append_change("hub_1", "project", "x") > seqs[-1])
fresh.close()

# ------------------------------------------- the progress pages under /stack/
print("\n-- the progress pages: routes, CSP, public artefacts --")


def raw_get(path, method="GET"):
    """(status, headers, body) without following redirects or raising."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(f"{BASE}{path}", method=method)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


CSP_WANTED = ("default-src 'self'; script-src 'self'; style-src 'self'; "
              "style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; "
              "connect-src 'self'; frame-ancestors 'none'")
for label, page, marker in (
        ("the project page", "/stack/p/horsehead-sho", b"project-body"),
        ("a filter page", "/stack/p/horsehead-sho/filter/HA@osc", b"filter-body"),
        ("a filter page with an escaped stream key", "/stack/p/horsehead-sho/filter/HA%40osc", b"filter-body"),
        ("the combined page", "/stack/p/horsehead-sho/combined", b"combined-body")):
    status, headers, body = raw_get(page)
    case(f"{label} is HTML with the CSP",
         status == 200 and headers.get("Content-Type", "").startswith("text/html")
         and marker in body and headers.get("Content-Security-Policy") == CSP_WANTED
         and headers.get("X-Content-Type-Options") == "nosniff",
         f"{status} {headers.get('Content-Type')}")
status, headers, _ = raw_get("/stack/p/x")
case("a slug that cannot exist is a 404 (still under the CSP)",
     status == 404 and headers.get("Content-Security-Policy") == CSP_WANTED)
status, headers, body = raw_get("/stack/js/pages/project.js")
case("a page script is served as a script under the CSP",
     status == 200 and b"parseProjectPath" in body and "javascript" in headers.get("Content-Type", "")
     and headers.get("Content-Security-Policy") == CSP_WANTED, headers.get("Content-Type", ""))
status, headers, body = raw_get("/stack/js/site-config.js")
case("site-config.js points the pages at /stack/api and /stack/public",
     status == 200 and b'"/stack/api"' in body and b'"/stack/public"' in body)
status, headers, _ = raw_get("/stack/css/tokens.css")
case("a stylesheet is served as CSS", status == 200 and "text/css" in headers.get("Content-Type", ""))
status, headers, _ = raw_get("/stack/")
case("/stack/ redirects somewhere useful", status == 302 and headers.get("Location", "").startswith("/"),
     str(headers.get("Location")))

PUB = DATA / "stack" / "public" / "prj_x"
(PUB / "deepzoom" / "HA" / "g000001" / "image_files" / "0").mkdir(parents=True)
(PUB / "HA" / "gold").mkdir(parents=True)
(PUB / "status.json").write_text('{"schema_version": 1}')
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
(PUB / "HA" / "gold" / "latest.png").write_bytes(PNG)
(PUB / "deepzoom" / "HA" / "g000001" / "image.dzi").write_text("<Image/>")
(PUB / "deepzoom" / "HA" / "g000001" / "image_files" / "0" / "0_0.jpg").write_bytes(b"\xff\xd8\xff")
(DATA / "stack" / "secret.txt").write_text("not for the web")

status, headers, body = raw_get("/stack/public/prj_x/status.json")
case("a pushed status.json is served as JSON for a minute",
     status == 200 and json.loads(body) == {"schema_version": 1}
     and headers.get("Content-Type", "").startswith("application/json")
     and headers.get("Cache-Control") == "public, max-age=60", str(headers.get("Cache-Control")))
case("...under the CSP and nosniff", headers.get("Content-Security-Policy") == CSP_WANTED
     and headers.get("X-Content-Type-Options") == "nosniff")
status, headers, body = raw_get("/stack/public/prj_x/HA/gold/latest.png")
case("a preview PNG is served as image/png for a minute",
     status == 200 and body == PNG and headers.get("Content-Type") == "image/png"
     and headers.get("Cache-Control") == "public, max-age=60")
status, headers, body = raw_get("/stack/public/prj_x/deepzoom/HA/g000001/image_files/0/0_0.jpg")
case("a deep-zoom tile is image/jpeg and immutable",
     status == 200 and headers.get("Content-Type") == "image/jpeg"
     and headers.get("Cache-Control") == "public, max-age=31536000, immutable")
status, headers, _ = raw_get("/stack/public/prj_x/deepzoom/HA/g000001/image.dzi")
case("...and its descriptor is XML and immutable",
     status == 200 and headers.get("Content-Type") == "application/xml"
     and "immutable" in headers.get("Cache-Control", ""))
status, _, _ = raw_get("/stack/public/prj_x/HA/gold/latest.png", method="HEAD")
case("HEAD works on a public file", status == 200)
status, _, _ = raw_get("/stack/public/prj_x/missing.json")
case("a missing public file is a 404", status == 404)
status, _, _ = raw_get("/stack/public/prj_nobody/status.json")
case("...and so is a project that has pushed nothing", status == 404)
for label, bad in (("a dot-dot path", "/stack/public/prj_x/../secret.txt"),
                   ("an encoded dot-dot", "/stack/public/prj_x/%2e%2e/secret.txt"),
                   ("a dot-dot inside the folder", "/stack/public/prj_x/HA/../../secret.txt"),
                   ("an encoded slash", "/stack/public/prj_x/..%2fsecret.txt"),
                   ("a doubled slash (an absolute path)", "/stack/public/prj_x//etc/hosts"),
                   ("a backslash", "/stack/public/prj_x/..%5csecret.txt"),
                   ("a dot-dot as the project", "/stack/public/../secret.txt"),
                   ("an encoded dot-dot as the project", "/stack/public/%2e%2e/secret.txt")):
    status, _, body = raw_get(bad)
    case(f"{label} is refused", status in (400, 404) and b"not for the web" not in body, str(status))

status, headers, _ = raw_get("/download")
case("/download is untouched: no CSP header", "Content-Security-Policy" not in headers
     and status in (200, 404))
status, headers, _ = raw_get("/sky")
case("/sky has none either", "Content-Security-Policy" not in headers)
status, headers, _ = raw_get("/api/v1/health")
case("...nor does the API", "Content-Security-Policy" not in headers)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
