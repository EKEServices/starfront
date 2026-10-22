#!/usr/bin/env python3
"""Bring up the whole live-stacking system on this Mac, for a home-network test.

    python3 tools/local_night.py                      # start everything, print the URLs, tail the logs
    python3 tools/local_night.py --synthetic          # ...and feed it synthetic frames, as a telescope would
    python3 tools/local_night.py --synthetic --exit-when-stacked   # the gate: stack, then tear down, exit 0

Why it exists: live stacking is five programs on two or three machines (the
Starfront server, a hub, a worker, an uploader, N.I.N.A.), and starting them
by hand means a stand-in Discord, a hub registration, two TOML files, an enrol
code and a collaboration bound to the hub, in the right order. This is the one
command that does all of it, so a reviewer can see a stack appear without
cloud accounts and the author can point the Windows PC and N.I.N.A. at it.

What it starts, all under one --data folder (default ~/starfront-local):

  1. server/run_local.py --lan   the Starfront server with its stand-in Discord
                                 (owner ncedelliott), port 8800
  2. collab-hub run              registered to that server (a hub-credentials.toml
                                 at 0600 and a collab-hub.toml written here), port 8790
  3. collab-worker run           enrolled to the hub, idle policy "always"

It also signs in as the owner, binds a throw-away collaboration to the hub
(the project page URL is printed), and prints what the Windows PC needs: the
worker enrol command, the ServerUrl for the N.I.N.A. plugin and the three URLs.

--synthetic generates frames with `collab-worker synth-frames` and uploads them
with `collab-uploader` through the Starfront front door (/stack/api), using the
grant a telescope gets from the agent listing. The synthetic sky is fake, so the
hub is given no XPSD folder and the worker is told to use the synthetic
catalogue (COLLAB_LOCAL_CATALOG=synthetic, the same knob scripts/local-e2e.sh
uses); a real Gaia catalogue could never solve those stars. Without --synthetic
the hub uses ~/Astronomy/XPSD when it exists.

--exit-when-stacked waits until the hub has pushed a live preview to
STARFRONT_DATA/stack/public/<project_id>/status.json, then stops everything and
exits 0 (non-zero on timeout or if any program dies).

What it does not do: build or publish anything for release (it runs the three
binaries from a Rapid Stacker Collab checkout, building them with cargo unless
--no-build), touch the cloud (the server is a local stand-in anyone on the
network can sign in to as anyone: local testing only), install the worker as a
service, or edit your Starfront desktop settings. Like run_local.py it refuses a
real server's data folder. The standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_RSC = ROOT.parent / "Rust" / "rapid-stacker-collab"
# The default above is relative to the main checkout (Astro/starfront); a
# worktree sits one level deeper, so fall back to the well-known location.
FALLBACK_RSC = Path.home() / "Develop" / "EKEServices" / "Astro" / "Rust" / "rapid-stacker-collab"

OWNER = "ncedelliott"
BINARIES = ("collab-hub", "collab-worker", "collab-uploader")

#: The synthetic scenario's field (collab-worker synth-frames), in degrees.
SYNTH_RA, SYNTH_DEC = 85.25, -2.46

_passed = True


def say(text: str = "") -> None:
    print(text, flush=True)


def fail(text: str) -> None:
    print(f"local_night: {text}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ------------------------------------------------------------------ processes
class Fleet:
    """The child programs, started in their own process groups so a stop takes
    their children (the worker's prepare helpers) with them."""

    def __init__(self, logs: Path):
        self.logs = logs
        self.procs: dict[str, subprocess.Popen] = {}
        logs.mkdir(parents=True, exist_ok=True)

    def start(self, name: str, argv: list[str], env: dict[str, str] | None = None,
              cwd: Path | None = None) -> subprocess.Popen:
        log = open(self.logs / f"{name}.log", "ab", buffering=0)
        merged = dict(os.environ)
        merged.update(env or {})
        proc = subprocess.Popen([str(a) for a in argv], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=merged, cwd=cwd,
                                start_new_session=True)
        self.procs[name] = proc
        return proc

    def dead(self) -> str:
        """The name of a program that has exited, or ''."""
        for name, proc in self.procs.items():
            if proc.poll() is not None:
                return f"{name} exited with {proc.returncode} (see {self.logs / (name + '.log')})"
        return ""

    def stop(self) -> None:
        for name in reversed(list(self.procs)):
            proc = self.procs[name]
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.time() + 15
        for proc in self.procs.values():
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        self.procs.clear()


def tail(logs: Path, names: list[str], stop: threading.Event) -> None:
    """Print new lines of each log, prefixed with its name, until stopped."""
    files = {}
    while not stop.is_set():
        for name in names:
            path = logs / f"{name}.log"
            if name not in files and path.exists():
                handle = open(path, "r", errors="replace")
                handle.seek(0, os.SEEK_END)
                files[name] = handle
            handle = files.get(name)
            while handle is not None:
                line = handle.readline()
                if not line:
                    break
                print(f"[{name}] {line.rstrip()}", flush=True)
        stop.wait(0.5)


# ----------------------------------------------------------------------- HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(method: str, url: str, body=None, token: str = "", timeout: float = 60):
    """One request; returns (status, parsed JSON or bytes). Never raises on a
    status, only on a connection that cannot be made."""
    head = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        head["Content-Type"] = "application/json"
    if token:
        head["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, payload, kind = response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        status, payload, kind = exc.code, exc.read(), exc.headers.get("Content-Type", "")
    if "json" in kind:
        try:
            return status, json.loads(payload.decode() or "{}")
        except ValueError:
            pass
    return status, payload


def hop(url: str) -> str:
    """Fetch a URL without following redirects; returns its Location."""
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        opener.open(urllib.request.Request(url), timeout=15)
    except urllib.error.HTTPError as exc:
        return exc.headers.get("Location", "")
    return ""


def wait_for(what: str, probe, fleet: Fleet, timeout: float, every: float = 0.5):
    """Call probe() until it returns something truthy; fail if a program dies."""
    end = time.time() + timeout
    while time.time() < end:
        gone = fleet.dead()
        if gone:
            fail(f"while waiting for {what}: {gone}")
        try:
            found = probe()
        except (OSError, ValueError, urllib.error.URLError):
            found = None
        if found:
            return found
        time.sleep(every)
    fail(f"timed out after {int(timeout)} s waiting for {what}")


def sign_in_as(base: str, name: str) -> str:
    """The device-code sign-in through the stand-in Discord, as `name`;
    returns the program token (what the desktop and the agent keep)."""
    status, begun = http("POST", f"{base}/api/v1/auth/login")
    if status != 200:
        fail(f"sign-in could not start: {status} {begun}")
    to_discord = hop(begun["url"])
    if "/oauth2/authorize" not in to_discord:
        fail(f"sign-in did not lead to the stand-in Discord: {to_discord!r}")
    approve = to_discord.replace("/oauth2/authorize", "/oauth2/approve")
    approve += ("&" if "?" in approve else "?") + urllib.parse.urlencode({"name": name})
    back = hop(approve)
    if not back:
        fail("the stand-in Discord did not send the browser back")
    urllib.request.urlopen(back, timeout=15).read()
    status, polled = http("GET", f"{base}/api/v1/auth/poll?code={begun['code']}")
    if status != 200 or not isinstance(polled, dict) or not polled.get("token"):
        fail(f"sign-in did not complete: {status} {polled}")
    return polled["token"]


# --------------------------------------------------------------------- set up
def toml_str(value) -> str:
    return json.dumps(str(value))


def find_rsc(arg: str | None) -> Path:
    for candidate in ([Path(arg)] if arg else [DEFAULT_RSC, FALLBACK_RSC]):
        if (candidate / "worker" / "Cargo.toml").exists():
            return candidate.resolve()
    fail("no Rapid Stacker Collab checkout found; pass --rsc DIR")


def find_binaries(rsc: Path, build: bool) -> dict[str, Path]:
    target = rsc / "worker" / "target" / "release"
    if build:
        say("building collab-hub, collab-worker and collab-uploader (cargo build --release)...")
        env = dict(os.environ, CARGO_NET_GIT_FETCH_WITH_CLI="true")
        code = subprocess.call(["cargo", "build", "--release", "-p", "collab-hub", "-p", "collab-worker",
                                "-p", "collab-uploader"], cwd=rsc / "worker", env=env)
        if code != 0:
            fail("cargo build failed")
    found = {name: target / name for name in BINARIES}
    missing = [n for n, p in found.items() if not p.exists()]
    if missing:
        fail(f"missing {', '.join(missing)} in {target}; run without --no-build")
    return found


def refuse_real_data(data: Path) -> None:
    """The same refusals as server/run_local.py, before anything is written."""
    from server import auth
    from astrocontrol.config import data_root
    real = (data_root() / "collab").resolve()
    if (data / auth.ENV_FILE).exists():
        fail(f"{data} has a {auth.ENV_FILE}, so it is a real server's folder. Give --data a folder of its own.")
    if data == real or real in data.parents:
        fail(f"{data} is where a real server keeps its database. Give --data a folder of its own.")


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def write_hub_files(hub_dir: Path, *, hub_id: str, hub_token: str, grant_secret: str, server_local: str,
                    bind_port: int, public: str, xpsd: Path | None, host: str) -> Path:
    hub_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"bind = {toml_str(f'0.0.0.0:{bind_port}')}",
             f"public_url = {toml_str(public)}",
             f"data_root = {toml_str(hub_dir / 'data')}"]
    if xpsd is not None:
        lines.append(f"xpsd_dir = {toml_str(xpsd)}")
        lines += ["", "[xpsd_paths]", f"macos = {toml_str(xpsd)}",
                  f"windows = {toml_str(chr(92) * 2 + host + chr(92) + 'XPSD')}"]
    lines += ["", "[starfront]", f"url = {toml_str(server_local)}", f"hub_id = {toml_str(hub_id)}"]
    config = hub_dir / "collab-hub.toml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    creds = hub_dir / "hub-credentials.toml"
    fd = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"hub_id = {toml_str(hub_id)}\n"
                     f"hub_token = {toml_str(hub_token)}\ngrant_secret = {toml_str(grant_secret)}\n")
    os.chmod(creds, 0o600)
    (hub_dir / "data").mkdir(exist_ok=True)
    return config


def set_toml_keys(path: Path, keys: dict[str, str]) -> None:
    """Replace or add top-level keys of a flat TOML file (the worker's)."""
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0].strip()
        if name not in keys:
            kept.append(line)
    # Top-level keys must come before any [table].
    first_table = next((i for i, l in enumerate(kept) if l.lstrip().startswith("[")), len(kept))
    added = [f"{k} = {v}" for k, v in keys.items()]
    path.write_text("\n".join(kept[:first_table] + added + kept[first_table:]) + "\n", encoding="utf-8")


def run_out(argv: list[str], **kw) -> str:
    done = subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)
    if done.returncode != 0:
        fail(f"{' '.join(str(a) for a in argv[:3])} failed ({done.returncode}): {done.stderr.strip()[-400:]}")
    return done.stdout.strip()


# ------------------------------------------------------------------ synthetic
def upload_synthetic(bins: dict[str, Path], frames: Path, data: Path, api_base: str, grant: str,
                     pid: str, log: Path) -> None:
    """Upload each synthetic contributor's folder with collab-uploader, through
    the front door, signed in with the telescope's grant (COLLAB_TOKEN)."""
    manifest = json.loads((frames / "manifest.json").read_text())
    env = dict(os.environ, COLLAB_TOKEN=grant)
    for who in manifest["contributors"]:
        folder = frames / who["slug"]
        conf = data / "uploader" / who["slug"]
        conf.mkdir(parents=True, exist_ok=True)
        base = [bins["collab-uploader"], "--api-base", api_base, "--config-dir", conf]
        lights = sorted(folder.glob("light_*.fits"))
        if not lights:
            continue
        steps = [
            base + ["projects"],
            base + ["rig", "confirm", "--from", lights[0], "--yes"],
            base + ["upload", folder, "--project", pid, "--include-calibration", "--now"],
        ]
        for step in steps:
            say(f"  uploader[{who['slug']}] {' '.join(str(a) for a in step[len(base):len(base) + 2] if not str(a).startswith('/'))}")
            done = subprocess.run([str(a) for a in step], env=env, capture_output=True, text=True)
            with open(log, "a") as handle:
                handle.write(f"$ {' '.join(str(a) for a in step)}\n{done.stdout}{done.stderr}\n")
            # Exit 2 is "finished, some files failed": the synthetic set carries bad
            # files on purpose (a truncated one, an off-field one), so it is fine.
            if done.returncode not in (0, 2) or (done.returncode == 2 and step[len(base)] != "upload"):
                fail(f"collab-uploader {step[len(base)]} for {who['slug']} failed ({done.returncode}); see {log}\n"
                     + (done.stderr or done.stdout)[-600:])
            if step[len(base)] == "upload":
                say("    " + (done.stdout.strip().splitlines() or [""])[-1])


def preview_published(public: Path) -> str:
    """What the hub's push has put in the public tree: '' until status.json
    parses AND a stream's live preview (<stream>/live/latest.png) is there."""
    status = public / "status.json"
    if not status.exists():
        return ""
    try:
        json.loads(status.read_text())
    except ValueError:
        return ""
    images = sorted(public.glob("*/live/latest.png"))
    return str(images[0].relative_to(public)) if images else ""


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the whole live-stacking system on this Mac")
    parser.add_argument("--rsc", help="Rapid Stacker Collab checkout (default: the sibling Rust/rapid-stacker-collab)")
    parser.add_argument("--no-build", action="store_true", help="use the binaries already built in RSC")
    parser.add_argument("--data", default=str(Path.home() / "starfront-local"),
                        help="everything lives here (default ~/starfront-local)")
    parser.add_argument("--port", type=int, default=8800, help="server port (default 8800)")
    parser.add_argument("--hub-port", type=int, default=8790, help="hub port (default 8790)")
    parser.add_argument("--name", default=OWNER, help=f"the owner's stand-in Discord name (default {OWNER})")
    parser.add_argument("--synthetic", action="store_true", help="upload synthetic frames as a telescope would")
    parser.add_argument("--exit-when-stacked", action="store_true",
                        help="wait for a published live preview, tear down, exit 0")
    parser.add_argument("--timeout", type=float, default=1500, help="seconds to wait for the stack (default 1500)")
    args = parser.parse_args(argv)
    if args.exit_when_stacked and not args.synthetic:
        parser.error("--exit-when-stacked needs --synthetic (nothing else makes frames)")

    from server.run_local import lan_addresses, local_name
    data = Path(args.data).expanduser().resolve()
    refuse_real_data(data)
    for port in (args.port, args.hub_port):
        if not port_free(port):
            fail(f"port {port} is already in use (an earlier run still going?)")
    rsc = find_rsc(args.rsc)
    bins = find_binaries(rsc, not args.no_build)

    data.mkdir(parents=True, exist_ok=True)
    logs = data / "logs"
    fleet = Fleet(logs)
    ips = lan_addresses()
    host = ips[0] if ips else local_name()
    server_public = f"http://{host}:{args.port}"
    server_local = f"http://127.0.0.1:{args.port}"
    hub_public = f"http://{host}:{args.hub_port}"
    hub_local = f"http://127.0.0.1:{args.hub_port}"
    stop_tail = threading.Event()
    result = 1

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        # 1. the server with its stand-in Discord
        say(f"starting the Starfront server (stand-in Discord, owner {args.name}) on port {args.port}...")
        fleet.start("server", [sys.executable, ROOT / "server" / "run_local.py", "--lan", "--data", data,
                               "--port", args.port, "--name", args.name], cwd=ROOT)
        wait_for("the server's health", lambda: http("GET", f"{server_local}/api/v1/health", timeout=3)[0] == 200,
                 fleet, 60)

        # 2. the owner signs in and registers a hub
        owner = sign_in_as(server_local, args.name)
        status, reg = http("POST", f"{server_local}/api/v1/hubs",
                           {"name": "Local hub", "lanUrl": hub_public}, token=owner)
        if status != 200 or not reg.get("hubToken"):
            fail(f"registering the hub failed: {status} {reg}")
        xpsd = Path.home() / "Astronomy" / "XPSD"
        use_xpsd = xpsd if (xpsd.is_dir() and not args.synthetic) else None
        hub_dir = data / "hub"
        config = write_hub_files(hub_dir, hub_id=reg["hubId"], hub_token=reg["hubToken"],
                                 grant_secret=reg["grantSecret"], server_local=server_local,
                                 bind_port=args.hub_port, public=hub_public, xpsd=use_xpsd, host=host.split(".")[0])
        say(f"starting collab-hub on port {args.hub_port} ({'XPSD ' + str(xpsd) if use_xpsd else 'no XPSD folder'})...")
        fleet.start("hub", [bins["collab-hub"], "run", "--config", config])
        wait_for("the hub's health", lambda: http("GET", f"{hub_local}/api/hub/v1/health", timeout=3)[0] == 200,
                 fleet, 60)

        # 3. a throw-away collaboration bound to the hub
        ra, dec = (SYNTH_RA, SYNTH_DEC) if args.synthetic else (84.0, 0.0)
        status, made = http("POST", f"{server_local}/api/v1/projects", {
            "name": "Local night", "region": {"ra": ra, "dec": dec, "width": 1.0, "height": 1.0},
            "requirements": {"filters": {"L": 1.0, "Ha": 1.0}},
            "stacking": {"backend": "hub", "hubId": reg["hubId"]}}, token=owner)
        if status != 200:
            fail(f"binding a collaboration to the hub failed: {status} {made}")
        sf_project = made["project"]["id"]
        # A telescope owned by the owner, to get the grant a telescope would hold.
        _, enrolled = http("POST", f"{server_local}/api/v1/agents", {"name": "Local scope"}, token=owner)
        scope = enrolled["token"]
        http("POST", f"{server_local}/api/v1/agent/hello", {"protocol": 1, "profile": {
            "name": "Local scope", "focalLength": 389.0, "pixelSize": 3.76, "sensorWidth": 9576,
            "sensorHeight": 6388, "binning": 1, "filters": {"Ha": 1.0}, "hoursPerNight": 6.0,
            "scale": 206.265 * 3.76 / 389.0,
            "field": [206.265 * 3.76 / 389.0 * 9576 / 3600.0, 206.265 * 3.76 / 389.0 * 6388 / 3600.0]}},
            token=scope)
        http("POST", f"{server_local}/api/v1/agent/projects/{sf_project}/join", {}, token=scope)
        _, browse = http("GET", f"{server_local}/api/v1/agent/projects", token=scope)
        block = next((p for p in browse["projects"] if p["id"] == sf_project), {}).get("stacking") or {}
        grant, pid = block.get("grant", ""), block.get("collabProjectId", "")
        if not grant or not pid:
            fail(f"the agent listing has no stacking grant for the project: {block}")
        front = f"{server_local}/stack/api"
        _, proj = http("GET", f"{front}/projects/{pid}", token=grant)
        slug = (proj.get("project") or {}).get("slug", "") if isinstance(proj, dict) else ""
        if args.synthetic:
            # The telescope joins as a member (the grant names the project, so no
            # code); the three synthetic contributors share its one membership, so
            # their filter names are mapped here, once.
            _, got = http("GET", f"{front}/projects/{pid}", token=grant)
            version = (got.get("project") or {}).get("terms_version", "") if isinstance(got, dict) else ""
            status, joined = http("POST", f"{front}/projects/{pid}/join", {
                "display_name": "Local scope", "accepted_terms_version": version,
                "filter_mapping": [{"raw": "Narrowband A", "canonical": "OIII"}]}, token=grant)
            if status != 200:
                fail(f"joining the stacking project as the telescope failed: {status} {joined}")

        # 4. a local worker
        code = run_out([bins["collab-hub"], "enrol-code", "--config", config, "--note", "Windows PC"])
        local_code = run_out([bins["collab-hub"], "enrol-code", "--config", config, "--note", "this Mac"])
        wdir = data / "worker"
        # The synthetic sky has no XPSD file, but the worker's configuration check still
        # wants a catalogue folder to exist: an empty one, never read (the synthetic
        # catalogue wins), set in the environment so it is not written to the file.
        wenv = {}
        if args.synthetic:
            unused = data / "worker" / "no-xpsd"
            unused.mkdir(parents=True, exist_ok=True)
            wenv = {"COLLAB_LOCAL_CATALOG": "synthetic", "COLLAB_XPSD_DIR": str(unused)}
        run_out([bins["collab-worker"], "enrol", hub_local, "--code", local_code, "--worker-id", "mac-worker",
                 "--dir", wdir], env=dict(os.environ, **wenv))
        scratch = wdir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        set_toml_keys(wdir / "collab-worker.toml", {"idle_policy": '"always"', "scratch_dir": toml_str(scratch)})
        say("starting collab-worker (idle policy always)...")
        fleet.start("worker", [bins["collab-worker"], "run", "--config", wdir / "collab-worker.toml"],
                    env=dict(wenv, RUST_LOG=os.environ.get("RUST_LOG", "info")))
        time.sleep(2)

        page = f"{server_public}/stack/p/{slug}"
        say("")
        say("=" * 72)
        say("  LOCAL NIGHT IS UP   (anyone on this network can sign in as anyone)")
        say("=" * 72)
        say(f"  server        {server_public}")
        say(f"  hub           {hub_public}")
        say(f"  project page  {page}")
        say(f"  collaboration {sf_project}   stacking project {pid}   slug {slug}")
        say(f"  data & logs   {data}   ({logs})")
        say("")
        say("  Windows PC, a second worker:")
        say(f"    collab-worker enrol {hub_public} --code {code}")
        say("    collab-worker service install")
        say("  N.I.N.A. plugin settings file:  ServerUrl = " + server_public)
        say("  Starfront desktop (local mode): collab.serverUrl = " + server_public)
        say(f"  (the enrol code is single-use and works for 1 h; mint more with: "
            f"{bins['collab-hub']} enrol-code --config {config})")
        say("")

        # 5. synthetic frames
        public_tree = data / "stack" / "public" / pid
        if args.synthetic:
            frames = data / "synthetic-frames"
            shutil.rmtree(frames, ignore_errors=True)
            say("generating synthetic frames (collab-worker synth-frames)...")
            run_out([bins["collab-worker"], "synth-frames", "--out", frames])
            say(f"uploading through the front door {server_public}/stack/api ...")
            upload_synthetic(bins, frames, data, f"{server_public}/stack/api", grant, pid,
                             logs / "uploader.log")
            say("uploaded. Waiting for the hub to relay, the worker to stack, and the hub to push a live preview...")

        if args.exit_when_stacked:
            begun = time.time()
            shown = 0.0
            while True:
                gone = fleet.dead()
                if gone:
                    fail(gone)
                found = preview_published(public_tree)
                if found:
                    say(f"live preview published: {public_tree / found} after {int(time.time() - begun)} s")
                    result = 0
                    break
                if time.time() - begun > args.timeout:
                    fail(f"no live preview in {public_tree} after {int(args.timeout)} s; see the logs in {logs}")
                if time.time() - shown > 30:
                    shown = time.time()
                    say(f"  ... {int(time.time() - begun)} s")
                time.sleep(2)
        else:
            say("Running. Ctrl-C stops everything. Logs follow.")
            threading.Thread(target=tail, args=(logs, ["server", "hub", "worker"], stop_tail), daemon=True).start()
            while True:
                gone = fleet.dead()
                if gone:
                    fail(gone)
                time.sleep(1)
    except KeyboardInterrupt:
        say("\nstopping...")
        result = 0
    finally:
        stop_tail.set()
        fleet.stop()
        say("everything stopped.")
    return result


if __name__ == "__main__":
    sys.exit(main())
