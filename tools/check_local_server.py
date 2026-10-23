"""Exercise server/run_local.py: the server with its stand-in Discord.

    python tools/check_local_server.py

The launcher is how the server, the program and the N.I.N.A. plugin are tried
out on one machine with no Discord application. It is started here as people
start it, in its own process, and signed in to over real HTTP the way the
program and the plugin do it: ask for a login code, open the page it names,
follow Discord's redirects, poll for the token.

What it must get right, besides working: the stand-in signs in whoever asks,
so it must never be pointed at a real server's data, never listen beyond this
machine, never send a browser anywhere but the server's own callback, and
never let a real Discord secret already exported in the shell leak into it.
With --lan it listens on the network, and that is checked too: reachable at
this machine's LAN address, and only there when --lan is not given.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "server" / "run_local.py"

results = []


def case(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


def lan_ip():
    """This machine's address on the network, or None with no network. A UDP
    connect sends nothing; it only asks which local address would be used."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith(("127.", "169.254.", "0.")) else ip


def local_name():
    """This machine's .local name, as run_local.py hands it out."""
    return socket.gethostname().split(".")[0] + ".local"


def reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def get(url, data=None):
    """(status, location, body) without following redirects."""
    try:
        with opener.open(urllib.request.Request(url, data=data), timeout=20) as r:
            return r.status, r.headers.get("Location", ""), r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", ""), exc.read().decode()


class Launched(subprocess.Popen):
    """The launcher, with its output read as it is written.

    Nothing read the pipe until the server was stopped, which is fine while the
    log fits the pipe (64 KB on Linux and macOS) and a hang once it does not: a
    Windows pipe holds about 4 KB, the server blocked writing an access-log line
    and the next request timed out. A reader thread keeps the pipe empty.
    """

    def __init__(self, args, **kw):
        super().__init__(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)
        self._lines: list[str] = []
        self._reader = threading.Thread(target=lambda: self._lines.extend(self.stdout), daemon=True)
        self._reader.start()

    def output(self) -> str:
        self._reader.join(timeout=2)
        return "".join(self._lines)

    def stop(self) -> str:
        self.terminate()
        try:
            self.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()
            self.wait()
        self._reader.join(timeout=10)
        return "".join(self._lines)


def launcher_env():
    env = dict(os.environ)
    # Keep the program's own data folder out of it, as every check does.
    env["ASTRO_DATA_DIR"] = tempfile.mkdtemp()
    # A shell that has the real server's settings exported: none of it may
    # reach the stand-in.
    env["STARFRONT_DISCORD_CLIENT_SECRET"] = "the-real-secret"
    env["STARFRONT_DISCORD_API"] = "https://discord.com/api/v10"
    env["ASTROCOLLAB_DISCORD_GUILD"] = "the-real-guild"
    env["STARFRONT_DISCORD_OWNERS"] = "1234"
    return env


def run_briefly(*args):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], env=launcher_env(),
                          capture_output=True, text=True, timeout=60)


# ------------------------------------------------------------- refusals
real = Path(tempfile.mkdtemp())
(real / "discord.env").write_text("STARFRONT_DISCORD_CLIENT_ID=x\n", encoding="utf-8")
done = run_briefly("--data", str(real), "--port", str(free_port()))
case("a folder with a discord.env is a real server's, and is refused",
     done.returncode != 0 and "discord.env" in done.stderr, done.stderr.strip()[:120])
case("...and nothing was written to it", sorted(p.name for p in real.iterdir()) == ["discord.env"])

env = launcher_env()
default_home = Path(env["ASTRO_DATA_DIR"])
done = subprocess.run([sys.executable, str(LAUNCHER), "--data", str(default_home / "collab"),
                       "--port", str(free_port())], env=env,
                      capture_output=True, text=True, timeout=60)
case("the folder a real server would use by default is refused",
     done.returncode != 0 and "real server" in done.stderr, done.stderr.strip()[:120])

done = run_briefly("--host", "0.0.0.0")
case("there is no --host: it never listens beyond this machine",
     done.returncode != 0 and "--host" in done.stderr)

done = run_briefly("--lan", "--data", str(real), "--port", str(free_port()))
case("--lan does not get past a folder with a discord.env either",
     done.returncode != 0 and "discord.env" in done.stderr, done.stderr.strip()[:120])
case("...and still nothing was written to it", sorted(p.name for p in real.iterdir()) == ["discord.env"])

done = subprocess.run([sys.executable, str(LAUNCHER), "--lan", "--data", str(default_home / "collab"),
                       "--port", str(free_port())], env=env,
                      capture_output=True, text=True, timeout=60)
case("--lan does not get past the folder a real server would use",
     done.returncode != 0 and "real server" in done.stderr, done.stderr.strip()[:120])

done = run_briefly("--name", "<script>")
case("an owner name Discord could not have is refused", done.returncode != 0)

# ------------------------------------------------------------- a sign-in
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path(tempfile.mkdtemp()) / "local"
server = Launched([sys.executable, str(LAUNCHER), "--port", str(PORT), "--data", str(DATA)],
                  env=launcher_env())
try:
    for _ in range(150):
        try:
            urllib.request.urlopen(f"{BASE}/api/v1/health", timeout=1).read()
            break
        except Exception:
            if server.poll() is not None:
                break
            time.sleep(0.2)
    if not case("the launcher starts and serves", server.poll() is None):
        print(server.stop())
        sys.exit(1)

    status = json.loads(get(f"{BASE}/api/v1/auth")[2])
    case("the server says Discord sign-in is on, public at its own address",
         status["discord"] and status["publicUrl"] == BASE, str(status))
    case("the data folder was made and holds the database",
         (DATA / "collab.sqlite").is_file())

    ip = lan_ip()
    case("without --lan the server is not reachable at this machine's network address",
         ip is None or not reachable(ip, PORT), f"{ip}:{PORT}")

    def sign_in(name=None, cancel=False):
        """The program's and the plugin's sign-in, step by step."""
        login = json.loads(get(f"{BASE}/api/v1/auth/login", data=b"")[2])
        _, to_discord, _ = get(login["url"])
        code, page_url, page = get(to_discord)
        steps = {"login": login, "authorize": to_discord, "page": page, "pageStatus": code}
        form = dict(re.findall(r'name="(redirect_uri|state)" value="([^"]*)"', page))
        form = {k: v.replace("&amp;", "&") for k, v in form.items()}
        if cancel:
            url = f"{BASE}/_local/discord/oauth2/deny?" + urllib.parse.urlencode(form)
        else:
            url = f"{BASE}/_local/discord/oauth2/approve?" + urllib.parse.urlencode(
                {**form, **({"name": name} if name is not None else {})})
        steps["approve"] = get(url)
        steps["callback"] = get(steps["approve"][1]) if steps["approve"][1] else None
        steps["poll"] = json.loads(get(f"{BASE}/api/v1/auth/poll?code={login['code']}")[2])
        return steps

    first = sign_in(name="ncedelliott")
    case("the sign-in page is the server's own, under /_local/discord",
         first["authorize"].startswith(f"{BASE}/_local/discord/oauth2/authorize?"),
         first["authorize"][:80])
    case("...and offers @ncedelliott, the owner, by default",
         first["pageStatus"] == 200 and 'value="ncedelliott"' in first["page"]
         and "@ncedelliott" in first["page"])
    case("Continue goes back to the server's own callback",
         first["approve"][0] == 303
         and first["approve"][1].startswith(f"{BASE}/auth/discord/callback?"))
    case("the callback says who signed in",
         first["callback"] and first["callback"][0] == 200
         and "Signed in as ncedelliott" in first["callback"][2])
    who = first["poll"]
    case("the poll hands over the token: ncedelliott, owner, may start collaborations",
         who.get("state") == "done" and who["user"]["name"] == "ncedelliott"
         and who["user"]["admin"] is True and who["user"]["canStart"] is True, str(who.get("user")))

    me = urllib.request.urlopen(urllib.request.Request(
        f"{BASE}/api/v1/auth/me", headers={"Authorization": f"Bearer {who['token']}"}),
        timeout=10)
    me = json.loads(me.read())
    case("the token works on the server's own API", me["name"] == "ncedelliott" and me["admin"])

    again = sign_in(name="ncedelliott")
    case("signing in again is the same person (the id is the same every run)",
         again["poll"]["user"]["id"] == who["user"]["id"])

    other = sign_in(name="@Bob")
    case("another name typed on the page is an ordinary member, not the owner",
         other["poll"].get("state") == "done" and other["poll"]["user"]["name"] == "bob"
         and other["poll"]["user"]["admin"] is False
         and other["poll"]["user"]["id"] != who["user"]["id"], str(other["poll"].get("user")))

    bad = sign_in(name="no spaces <b>")
    case("a name Discord could not have is turned away on the page",
         bad["approve"][0] == 400 and bad["poll"].get("state") == "pending")

    cancelled = sign_in(cancel=True)
    case("Cancel goes back to the server, which says the sign-in was cancelled",
         cancelled["callback"] and cancelled["callback"][0] == 400
         and "cancelled" in cancelled["callback"][2].lower()
         and cancelled["poll"].get("state") == "pending")

    login = json.loads(get(f"{BASE}/api/v1/auth/login", data=b"")[2])
    evil = urllib.parse.urlencode({"redirect_uri": "https://example.com/steal", "state": login["code"],
                                   "name": "ncedelliott"})
    for step in ("authorize", "approve", "deny"):
        status, location, _ = get(f"{BASE}/_local/discord/oauth2/{step}?{evil}")
        case(f"{step} never sends the browser anywhere but the server's callback",
             status == 400 and not location)

    status, _, _ = get(f"{BASE}/_local/discord/oauth2/token",
                       data=urllib.parse.urlencode({"code": "made-up", "client_secret": "the-real-secret",
                                                    "redirect_uri": f"{BASE}/auth/discord/callback"}).encode())
    case("the token exchange takes neither a made-up code nor the shell's secret", status == 400)
    status, _, _ = get(f"{BASE}/_local/discord/users/@me")
    case("who-am-I needs a token the stand-in issued", status == 401)
finally:
    out = server.stop()

case("the banner names the stand-in and its owner",
     "Stand-in Discord" in out and "@ncedelliott owns this server" in out)
case("the banner says Discord sign-in is on, at this machine only",
     "sign-in on, guild local" in out and f"http://127.0.0.1:{PORT}" in out)
case("the shell's real Discord settings were not used",
     "the-real-guild" not in out and "the-real-secret" not in out)

# ------------------------------------------------------------- --lan
LAN_PORT = free_port()
LAN_DATA = Path(tempfile.mkdtemp()) / "lan"
lan = Launched([sys.executable, str(LAUNCHER), "--lan", "--port", str(LAN_PORT),
                "--data", str(LAN_DATA)],
               env=launcher_env())
try:
    local = f"http://127.0.0.1:{LAN_PORT}"
    for _ in range(150):
        try:
            urllib.request.urlopen(f"{local}/api/v1/health", timeout=1).read()
            break
        except Exception:
            if lan.poll() is not None:
                break
            time.sleep(0.2)
    if not case("--lan starts and serves", lan.poll() is None):
        print(lan.stop())
        sys.exit(1)

    ip = lan_ip()
    lan_host = ip or local_name()
    lan_base = f"http://{lan_host}:{LAN_PORT}"
    if ip:
        case("--lan listens on the network: reachable at this machine's LAN address",
             reachable(ip, LAN_PORT), f"{ip}:{LAN_PORT}")
    else:
        case("--lan: no network here, so the server is checked as configured",
             True, "no LAN address; host 0.0.0.0 is checked from the banner below")
    status = json.loads(get(f"{local}/api/v1/auth")[2])
    case("--lan hands out the LAN address as its public address, not loopback",
         status["publicUrl"] == lan_base and "127.0.0.1" not in status["publicUrl"],
         status["publicUrl"])
    login = json.loads(get(f"{local}/api/v1/auth/login", data=b"")[2])
    case("--lan sign-in links point at the LAN address, so another PC can open them",
         login["url"].startswith(f"{lan_base}/auth/discord/start?"), login["url"][:80])
    authorize = get(f"{local}{login['url'][len(lan_base):]}")[1]
    case("--lan: the stand-in's sign-in page is at the LAN address too",
         authorize.startswith(f"{lan_base}/_local/discord/oauth2/authorize?"), authorize[:80])
finally:
    lan_out = lan.stop()

case("--lan binds every interface (0.0.0.0), and says so",
     f"listening http://0.0.0.0:{LAN_PORT}" in lan_out)
case("--lan prints a loud warning that anyone on the network can sign in as anyone",
     "WARNING" in lan_out and "can sign in as ANYONE" in lan_out)
case("--lan prints the LAN URL for the other PC",
     f"http://{lan_host}:{LAN_PORT}" in lan_out)
case("without --lan there is no warning and the bind stays loopback",
     "WARNING" not in out and f"listening http://127.0.0.1:{PORT}" in out)

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
