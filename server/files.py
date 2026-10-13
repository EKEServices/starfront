"""Where the subs go: the files side of a collaboration.

The ledger says what was shot; this is the data itself. A collaboration that
*collects files* takes every contributor's lights onto this server, one
folder per collaboration, one per person inside it, so the coordinator has
them in one place to stack.

Two ways in, one door. A browser: the files page at `/files`, where
somebody signs in with Discord, picks the collaboration, and drops the
night's folder on it. A program: the same `PUT`, on a telescope's token or a
person's, which is how the N.I.N.A. plugin and Starfront will send each
light the moment it is saved. The page exists so that people can start
tonight; the programs come after, and nothing here changes when they do.

A browser sign-in is its own credential - a *web token* in a cookie - so
that signing in here does not sign anybody's N.I.N.A. or Starfront out, as a
second program sign-in would.

What is kept is only ever written under the files folder, which lives beside
the database (`/var/lib/starfront/files` on the VPS, or wherever
`STARFRONT_FILES` points - a mounted volume, in time). Nothing is deleted by
the server on its own.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

#: What a light is. Compressed FITS (`.fits.fz`) ends in `.fz`; XISF is
#: PixInsight's. Nothing else: a stray `.txt` or a `.jpg` of the result is
#: not data for a stack.
ALLOWED = (".fits", ".fit", ".fts", ".fz", ".xisf")
#: The largest single file taken. A 61-megapixel sub is 120 MB; a 16-bit
#: full-frame at 2x2 binning is a quarter of that. Four gigabytes is nothing
#: anybody shoots in one exposure and keeps a runaway upload bounded.
MAX_BYTES = 4 * 1024 ** 3
#: Room left on the disk, always. The database lives on the same disk.
KEEP_FREE = 2 * 1024 ** 3
#: The cookie a browser sign-in lives in, and how long it lasts unused.
COOKIE = "starfront_web"
COOKIE_SECONDS = 90 * 86400

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_name(name: str) -> str:
    """A file name as it will be kept, or '' when it cannot be.

    The base name only, whatever path came with it; the characters no file
    system takes removed; and one of the extensions a light has.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _BAD_CHARS.sub("", base).strip(". ")
    if not base or len(base) > 200:
        return ""
    if not base.lower().endswith(ALLOWED):
        return ""
    return base


def looks_like_data(head: bytes, name: str) -> bool:
    """Whether the first bytes are what the extension says they are."""
    lower = name.lower()
    if lower.endswith(".xisf"):
        return head.startswith(b"XISF0100")
    if lower.endswith(".fz"):
        # A tile-compressed FITS is still a FITS: its primary header is plain.
        return head.startswith(b"SIMPLE")
    return head.startswith(b"SIMPLE")


class Files:
    """The routes, bound to the server's store and its idea of who is asking."""

    def __init__(self, app: FastAPI, store: Any, folder: Path,
                 person: Callable[[str], dict[str, Any] | None],
                 owns_project: Callable[[dict[str, Any], dict[str, Any]], bool],
                 owners: list[str], public_url: str,
                 new_login_code: Callable[[], str]) -> None:
        self.app = app
        self.store = store
        self.folder = Path(folder)
        self.person = person
        self.owns_project = owns_project
        self.owners = list(owners)
        self.public_url = public_url.rstrip("/")
        self.new_login_code = new_login_code
        self.folder.mkdir(parents=True, exist_ok=True)
        self._routes()

    # -- who is asking ---------------------------------------------------
    def who(self, request: Request) -> dict[str, Any] | None:
        """The person behind a request: a bearer token (a person's, or a
        telescope's, which stands for its owner), else the browser cookie."""
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if token:
            found = self.person(token)
            if found is not None:
                return found
            agent = self.store.agent_by_token(token)
            if agent is not None:
                owner = str(agent.get("owner_id") or "")
                user = self.store.user(owner) if owner else None
                if user is None:
                    raise HTTPException(status_code=403,
                                        detail="this telescope has no owner to file "
                                               "its lights under; sign in with Discord first")
                return {"kind": "agent", "id": user["id"], "name": user["name"],
                        "roles": [], "admin": user["id"] in self.owners,
                        "telescope": str(agent.get("name") or "")}
            return None
        cookie = request.cookies.get(COOKIE, "")
        user = self.store.user_by_web_token(cookie) if cookie else None
        if user is None:
            return None
        return {"kind": "web", "id": user["id"], "name": user["name"],
                "roles": list(user.get("roles") or []),
                "admin": user["id"] in self.owners}

    def require(self, request: Request) -> dict[str, Any]:
        who = self.who(request)
        if who is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return who

    # -- the shape of things ----------------------------------------------
    def path_of(self, project_id: str, user_id: str, name: str) -> Path:
        return self.folder / project_id / user_id / name

    def collecting(self, project: dict[str, Any]) -> bool:
        return bool((project.get("payload") or {}).get("collectFiles"))

    def stats(self) -> dict[str, Any]:
        totals = self.store.file_totals()
        try:
            usage = shutil.disk_usage(self.folder)
            free = int(usage.free)
        except OSError:
            free = 0
        return {"count": totals["count"], "bytes": totals["bytes"], "free": free}

    def _row(self, row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
        user = str(row.get("user") or "")
        if user not in names:
            found = self.store.user(user)
            names[user] = str((found or {}).get("name") or user)
        return {"name": row["name"], "bytes": int(row.get("bytes") or 0),
                "received": float(row.get("received") or 0.0),
                "telescope": row.get("telescope") or "",
                "user": user, "uploader": names[user]}

    def _project(self, project_id: str) -> dict[str, Any]:
        project = self.store.project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="no such collaboration")
        return project

    # -- the routes --------------------------------------------------------
    def _routes(self) -> None:
        app = self.app

        @app.get("/files")
        def files_page() -> HTMLResponse:
            return HTMLResponse(_PAGE)

        @app.get("/files/signin")
        def files_signin():
            """Begin a browser sign-in that comes back here."""
            code = self.new_login_code()
            self.store.add_login(code, back="/files")
            return RedirectResponse(f"/auth/discord/start?code={code}", status_code=303)

        @app.post("/files/signout")
        def files_signout(request: Request) -> JSONResponse:
            cookie = request.cookies.get(COOKIE, "")
            if cookie:
                self.store.forget_web_token(cookie)
            response = JSONResponse({"signedOut": True})
            response.delete_cookie(COOKIE, path="/")
            return response

        @app.get("/api/v1/files/projects")
        def files_projects(request: Request) -> dict[str, Any]:
            """The collaborations that collect files, with what each holds,
            and who is asking - the page's one call on load."""
            who = self.who(request)
            listed = []
            for project in self.store.projects():
                if project.get("status") != "open":
                    continue
                owned = who is not None and self.owns_project(who, project)
                if not self.collecting(project) and not owned:
                    continue
                totals = self.store.file_totals(project["id"])
                mine = (self.store.file_totals_of(project["id"], who["id"])
                        if who is not None else {"count": 0, "bytes": 0})
                listed.append({
                    "id": project["id"], "name": project["name"],
                    "coordinator": project.get("coordinator") or "",
                    "kind": (project.get("payload") or {}).get("kind") or "mosaic",
                    "collecting": self.collecting(project),
                    "notes": (project.get("payload") or {}).get("notes") or "",
                    "files": totals["count"], "bytes": totals["bytes"],
                    "myFiles": mine["count"], "myBytes": mine["bytes"],
                    "owned": owned,
                })
            return {
                "signedIn": who is not None,
                "name": who["name"] if who else "",
                "admin": bool(who and who["admin"]),
                "projects": listed,
                "allowed": list(ALLOWED),
                "maxBytes": MAX_BYTES,
                "free": self.stats()["free"],
            }

        @app.get("/api/v1/projects/{project_id}/files")
        def list_files(project_id: str, request: Request) -> dict[str, Any]:
            """What has arrived: everything, to the coordinator; your own,
            to you."""
            who = self.require(request)
            project = self._project(project_id)
            owned = self.owns_project(who, project)
            rows = self.store.files_in(project_id, None if owned else who["id"])
            names: dict[str, str] = {}
            listed = [self._row(row, names) for row in rows]
            return {"project": project_id, "name": project["name"],
                    "collecting": self.collecting(project), "owned": owned,
                    "me": who["id"], "files": listed,
                    "bytes": sum(row["bytes"] for row in listed),
                    "free": self.stats()["free"]}

        @app.put("/api/v1/projects/{project_id}/files/{name}")
        async def upload(project_id: str, name: str, request: Request,
                         telescope: str = Query(default="", max_length=60)) -> dict[str, Any]:
            """One file, streamed to disk as it arrives.

            Idempotent by name and size: a file already here at the same size
            is not taken again, which is what lets an upload that was cut off
            be run again over the whole folder. A different size replaces.
            """
            who = self.require(request)
            project = self._project(project_id)
            if project.get("status") != "open":
                raise HTTPException(status_code=409, detail="that collaboration is closed")
            if not self.collecting(project) and not self.owns_project(who, project):
                raise HTTPException(status_code=409,
                                    detail="that collaboration is not collecting files")
            clean = clean_name(name)
            if not clean:
                raise HTTPException(status_code=400,
                                    detail="not a light: a file name ending in "
                                           + ", ".join(ALLOWED) + " is expected")
            try:
                length = int(request.headers.get("content-length") or "-1")
            except ValueError:
                length = -1
            if length < 0:
                raise HTTPException(status_code=411, detail="the file's size must be sent")
            if length > MAX_BYTES:
                raise HTTPException(status_code=413,
                                    detail=f"larger than {MAX_BYTES // 1024 ** 3} GB")
            if length == 0:
                raise HTTPException(status_code=400, detail="an empty file")
            existing = self.store.file(project_id, who["id"], clean)
            final = self.path_of(project_id, who["id"], clean)
            if existing is not None and int(existing["bytes"]) == length and final.is_file():
                return {"file": self._row(existing, {}), "already": True}
            if self.stats()["free"] - length < KEEP_FREE:
                raise HTTPException(status_code=507,
                                    detail="the server has no room left for files; "
                                           "tell the coordinator")
            final.parent.mkdir(parents=True, exist_ok=True)
            part = final.with_name(final.name + ".part")
            written = 0
            head = b""
            try:
                with open(part, "wb") as handle:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        if len(head) < 80:
                            head += chunk[:80 - len(head)]
                        written += len(chunk)
                        if written > length:
                            raise HTTPException(status_code=400,
                                                detail="more arrived than the size said")
                        handle.write(chunk)
                if written != length:
                    raise HTTPException(status_code=400,
                                        detail=f"the upload stopped short "
                                               f"({written} of {length} bytes)")
                if not looks_like_data(head, clean):
                    raise HTTPException(status_code=400,
                                        detail=f"{clean} does not start like a "
                                               f"{'XISF' if clean.lower().endswith('.xisf') else 'FITS'} file")
                os.replace(part, final)
            except BaseException:
                with _quiet():
                    part.unlink()
                raise
            label = telescope.strip() or str(who.get("telescope") or "")
            row = self.store.add_file(secrets.token_hex(8), project_id, who["id"],
                                      clean, written, label[:60])
            return {"file": self._row(row, {}), "already": False}

        @app.get("/api/v1/projects/{project_id}/files/{user_id}/{name}")
        def download(project_id: str, user_id: str, name: str, request: Request):
            """A file back out: the coordinator's, or your own."""
            who = self.require(request)
            project = self._project(project_id)
            if who["id"] != user_id and not self.owns_project(who, project):
                raise HTTPException(status_code=403, detail="not your file")
            clean = clean_name(name)
            row = self.store.file(project_id, user_id, clean) if clean else None
            path = self.path_of(project_id, user_id, clean) if clean else None
            if row is None or path is None or not path.is_file():
                raise HTTPException(status_code=404, detail="no such file")
            return FileResponse(path, media_type="application/octet-stream", filename=clean)

        @app.delete("/api/v1/projects/{project_id}/files/{name}")
        def remove(project_id: str, name: str, request: Request,
                   user: str = Query(default="", max_length=64)) -> dict[str, Any]:
            """Take a file off: your own, or, as the coordinator, anybody's."""
            who = self.require(request)
            project = self._project(project_id)
            target = user or who["id"]
            if target != who["id"] and not self.owns_project(who, project):
                raise HTTPException(status_code=403, detail="not your file")
            clean = clean_name(name)
            if not clean or self.store.file(project_id, target, clean) is None:
                raise HTTPException(status_code=404, detail="no such file")
            with _quiet():
                self.path_of(project_id, target, clean).unlink()
            self.store.remove_file(project_id, target, clean)
            return {"removed": clean}


class _quiet:
    """`with _quiet(): ...` - a cleanup that must not raise over the real error."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> bool:
        return True


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Starfront files</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #0d1017; color: #dbe3ee; font: 15px/1.5 system-ui, sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 28px 20px 60px; }
  h1 { font-size: 24px; margin: 0 0 4px; }
  h2 { font-size: 16px; margin: 26px 0 8px; }
  p { margin: 6px 0; } .muted { color: #8f9bb0; } .small { font-size: 13px; }
  a { color: #9fe4bd; }
  .btn { display: inline-block; background: #7ee7a5; color: #0d1017; font-weight: 600; padding: 9px 16px;
         border-radius: 8px; border: 0; cursor: pointer; text-decoration: none; font-size: 15px; }
  .btn.ghost { background: #1a2130; color: #dbe3ee; }
  .btn:disabled { opacity: .5; cursor: default; }
  select, input[type=text] { background: #11161f; color: #dbe3ee; border: 1px solid #2a3344; border-radius: 8px;
         padding: 8px 10px; font-size: 15px; min-width: 260px; }
  .drop { margin: 12px 0; padding: 26px 18px; border: 2px dashed #2a3344; border-radius: 12px; text-align: center; }
  .drop.over { border-color: #7ee7a5; background: #11161f; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; margin: 8px 0; }
  th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid #1f2634; vertical-align: top; }
  th { color: #8f9bb0; font-weight: 500; }
  td.num, th.num { text-align: right; white-space: nowrap; }
  .bar { height: 8px; background: #1a2130; border-radius: 4px; overflow: hidden; }
  .bar > i { display: block; height: 100%; background: #7ee7a5; width: 0; }
  .ok { color: #7ee7a5; } .no { color: #ff8a7a; } .wait { color: #e5c07b; }
  .x { background: none; border: 0; color: #8f9bb0; cursor: pointer; font-size: 14px; }
  .x:hover { color: #ff8a7a; }
  #queue tr.done td { color: #8f9bb0; }
  code { background: #11161f; padding: 1px 5px; border-radius: 4px; }
</style></head>
<body><main>
<h1>Starfront files</h1>
<p class="muted">Where a collaboration's lights are collected for stacking. Drop the night's folder; only the FITS and XISF files in it are taken, and files already here are skipped, so you can drop the same folder again tomorrow.</p>

<section id="signin" hidden>
  <p>Sign in with the Discord account you use for Starfront. Signing in here does not sign your N.I.N.A. or Starfront out.</p>
  <p><a class="btn" href="/files/signin">Sign in with Discord</a></p>
</section>

<section id="app" hidden>
  <p class="row"><span>Signed in as <b id="who"></b></span> <button class="btn ghost" id="signout" style="padding:5px 10px;font-size:13px">Sign out</button></p>

  <h2>Collaboration</h2>
  <p class="row">
    <select id="project"></select>
    <span class="small muted" id="projectNote"></span>
  </p>
  <p id="none" class="muted" hidden>No collaboration is collecting files right now.</p>

  <div id="uploader" hidden>
    <h2>Your lights</h2>
    <div class="drop" id="drop">
      <p>Drop files or a folder here</p>
      <p class="row" style="justify-content:center">
        <button class="btn ghost" id="pickFiles">Choose files</button>
        <button class="btn ghost" id="pickFolder">Choose a folder</button>
        <input type="file" id="files" multiple hidden>
        <input type="file" id="folder" webkitdirectory multiple hidden>
      </p>
      <p class="small muted">Lights only, as N.I.N.A. saved them; the file names say which collaboration and panel. Keep this tab open while it uploads.</p>
    </div>
    <p class="row"><input type="text" id="telescope" placeholder="Telescope (optional)" style="min-width:220px"> <span class="small muted">written against each file, so the coordinator can tell rigs apart</span></p>
    <p id="progress" hidden><span id="progressText"></span></p>
    <div class="bar" id="allBar" hidden><i></i></div>
    <table id="queue" hidden><thead><tr><th>File</th><th class="num">Size</th><th>State</th></tr></thead><tbody></tbody></table>

    <h2 id="mineHead">Already here from you</h2>
    <p class="small muted" id="mineNote"></p>
    <table id="mine"><thead><tr><th>File</th><th>Telescope</th><th class="num">Size</th><th>Received</th><th></th></tr></thead><tbody></tbody></table>

    <div id="ownerBox" hidden>
      <h2>Everything on this collaboration</h2>
      <p class="small muted" id="allNote"></p>
      <table id="all"><thead><tr><th>Person</th><th>File</th><th>Telescope</th><th class="num">Size</th><th>Received</th><th></th></tr></thead><tbody></tbody></table>
    </div>
  </div>
</section>

<script>
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { projects: [], allowed: [], project: null, owned: false, running: false, queue: [] };

  const mb = (n) => n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(2) + ' GB' : (n / 1024 ** 2).toFixed(1) + ' MB';
  const when = (t) => t ? new Date(t * 1000).toLocaleString() : '';
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const api = async (path, options) => {
    const r = await fetch(path, options);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || r.statusText);
    return body;
  };
  const allowed = (name) => state.allowed.some((ext) => name.toLowerCase().endsWith(ext));

  async function load() {
    const info = await api('/api/v1/files/projects');
    state.projects = info.projects; state.allowed = info.allowed;
    $('signin').hidden = info.signedIn; $('app').hidden = !info.signedIn;
    if (!info.signedIn) return;
    $('who').textContent = info.name;
    const select = $('project');
    select.innerHTML = '';
    for (const p of info.projects) {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = `${p.name} — ${p.coordinator || 'no coordinator'} · ${p.files} file${p.files === 1 ? '' : 's'}, ${mb(p.bytes)}` + (p.collecting ? '' : ' (not collecting; yours)');
      select.appendChild(o);
    }
    $('none').hidden = info.projects.length > 0;
    $('uploader').hidden = info.projects.length === 0;
    const wanted = location.hash.slice(1) || localStorage.getItem('starfront-files-project') || '';
    if (info.projects.some((p) => p.id === wanted)) select.value = wanted;
    if (info.projects.length) await pick();
  }

  async function pick() {
    const id = $('project').value;
    state.project = state.projects.find((p) => p.id === id) || null;
    if (!state.project) return;
    localStorage.setItem('starfront-files-project', id);
    const p = state.project;
    $('projectNote').textContent = p.notes || '';
    const listing = await api(`/api/v1/projects/${encodeURIComponent(id)}/files`);
    state.owned = listing.owned;
    drawMine(listing);
    drawAll(listing);
  }

  function drawMine(listing) {
    const body = $('mine').querySelector('tbody');
    const my = listing.files.filter((f) => f.user === listing.me);
    body.innerHTML = my.map((f) => `<tr><td>${esc(f.name)}</td><td>${esc(f.telescope)}</td><td class="num">${mb(f.bytes)}</td><td>${when(f.received)}</td>
      <td><button class="x" data-del="${esc(f.name)}" title="Take it off the server">✕</button></td></tr>`).join('');
    $('mineNote').textContent = my.length ? `${my.length} file${my.length === 1 ? '' : 's'}, ${mb(my.reduce((s, f) => s + f.bytes, 0))}` : 'nothing yet';
    body.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm(`Remove ${b.dataset.del} from the server?`)) return;
      try { await api(`/api/v1/projects/${encodeURIComponent(state.project.id)}/files/${encodeURIComponent(b.dataset.del)}`, { method: 'DELETE' }); await pick(); }
      catch (e) { alert(e.message); }
    }));
  }

  function drawAll(listing) {
    $('ownerBox').hidden = !listing.owned;
    if (!listing.owned) return;
    const body = $('all').querySelector('tbody');
    body.innerHTML = listing.files.map((f) => `<tr><td>${esc(f.uploader)}</td>
      <td><a href="/api/v1/projects/${encodeURIComponent(state.project.id)}/files/${encodeURIComponent(f.user)}/${encodeURIComponent(f.name)}">${esc(f.name)}</a></td>
      <td>${esc(f.telescope)}</td><td class="num">${mb(f.bytes)}</td><td>${when(f.received)}</td>
      <td><button class="x" data-rm="${esc(f.name)}" data-user="${esc(f.user)}">✕</button></td></tr>`).join('');
    const people = new Set(listing.files.map((f) => f.user)).size;
    $('allNote').textContent = `${listing.files.length} file${listing.files.length === 1 ? '' : 's'} from ${people} ${people === 1 ? 'person' : 'people'}, ${mb(listing.bytes)}; ${mb(listing.free)} free on the server. Each link downloads one file; the whole set sits in the server's files folder under this collaboration's id.`;
    body.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm(`Remove ${b.dataset.rm}?`)) return;
      try { await api(`/api/v1/projects/${encodeURIComponent(state.project.id)}/files/${encodeURIComponent(b.dataset.rm)}?user=${encodeURIComponent(b.dataset.user)}`, { method: 'DELETE' }); await pick(); }
      catch (e) { alert(e.message); }
    }));
  }

  function take(fileList) {
    if (!state.project) return;
    const files = Array.from(fileList).filter((f) => allowed(f.name));
    const skipped = fileList.length - files.length;
    if (!files.length) { alert('None of those are FITS or XISF files.'); return; }
    for (const f of files) state.queue.push({ file: f, state: 'waiting', sent: 0 });
    if (skipped) $('progressText').textContent = `${skipped} file${skipped === 1 ? '' : 's'} left out (not lights).`;
    drawQueue();
    run();
  }

  function drawQueue() {
    $('queue').hidden = !state.queue.length;
    const body = $('queue').querySelector('tbody');
    body.innerHTML = state.queue.map((q) => `<tr class="${q.state === 'done' || q.state === 'already' ? 'done' : ''}"><td>${esc(q.file.name)}</td><td class="num">${mb(q.file.size)}</td>
      <td class="${q.state === 'failed' ? 'no' : (q.state === 'done' || q.state === 'already' ? 'ok' : 'wait')}">${esc(q.text || q.state)}</td></tr>`).join('');
    const total = state.queue.reduce((s, q) => s + q.file.size, 0);
    const sent = state.queue.reduce((s, q) => s + (q.state === 'done' || q.state === 'already' ? q.file.size : q.sent), 0);
    $('allBar').hidden = !state.queue.length;
    $('allBar').firstElementChild.style.width = total ? (100 * sent / total).toFixed(1) + '%' : '0';
    const left = state.queue.filter((q) => q.state === 'waiting' || q.state === 'sending').length;
    $('progress').hidden = false;
    if (left) $('progressText').textContent = `${left} to go · ${mb(sent)} of ${mb(total)}`;
    else if (state.queue.length) $('progressText').textContent = `All done: ${state.queue.filter((q) => q.state === 'done').length} uploaded, ${state.queue.filter((q) => q.state === 'already').length} already here, ${state.queue.filter((q) => q.state === 'failed').length} failed.`;
  }

  function send(q, attempt) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const tel = encodeURIComponent($('telescope').value.trim());
      xhr.open('PUT', `/api/v1/projects/${encodeURIComponent(state.project.id)}/files/${encodeURIComponent(q.file.name)}?telescope=${tel}`);
      xhr.upload.onprogress = (e) => { q.sent = e.loaded; drawQueue(); };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText || '{}'); } catch (e) { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300) { q.state = body.already ? 'already' : 'done'; q.text = body.already ? 'already here' : 'uploaded'; resolve(true); }
        else if (xhr.status === 507 || xhr.status === 400 || xhr.status === 409 || xhr.status === 413) { q.state = 'failed'; q.text = body.detail || xhr.statusText; resolve(true); }
        else resolve(false);
      };
      xhr.onerror = () => resolve(false);
      xhr.send(q.file);
    });
  }

  async function run() {
    if (state.running) return;
    state.running = true;
    try {
      for (const q of state.queue) {
        if (q.state !== 'waiting') continue;
        q.state = 'sending'; q.text = 'sending'; drawQueue();
        let ok = false;
        for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
          ok = await send(q, attempt);
          if (!ok) { q.text = `retrying (${attempt})`; drawQueue(); await new Promise((r) => setTimeout(r, 3000 * attempt)); }
        }
        if (!ok) { q.state = 'failed'; q.text = 'could not reach the server; drop the folder again later'; }
        drawQueue();
      }
      await pick();
    } finally {
      state.running = false;
    }
  }

  $('project').addEventListener('change', () => pick().catch((e) => alert(e.message)));
  $('pickFiles').addEventListener('click', () => $('files').click());
  $('pickFolder').addEventListener('click', () => $('folder').click());
  $('files').addEventListener('change', (e) => { take(e.target.files); e.target.value = ''; });
  $('folder').addEventListener('change', (e) => { take(e.target.files); e.target.value = ''; });
  const drop = $('drop');
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', async (e) => {
    e.preventDefault(); drop.classList.remove('over');
    const items = Array.from(e.dataTransfer.items || []);
    const files = [];
    const walk = async (entry) => {
      if (entry.isFile) { await new Promise((r) => entry.file((f) => { files.push(f); r(); }, r)); }
      else if (entry.isDirectory) {
        const reader = entry.createReader();
        const all = [];
        for (;;) { const batch = await new Promise((r) => reader.readEntries(r, () => r([]))); if (!batch.length) break; all.push(...batch); }
        for (const child of all) await walk(child);
      }
    };
    if (items.length && items[0].webkitGetAsEntry) { for (const it of items) { const entry = it.webkitGetAsEntry(); if (entry) await walk(entry); } }
    else files.push(...e.dataTransfer.files);
    take(files);
  });
  $('signout').addEventListener('click', async () => { await fetch('/files/signout', { method: 'POST' }); location.reload(); });
  window.addEventListener('beforeunload', (e) => { if (state.running) { e.preventDefault(); e.returnValue = ''; } });

  load().catch((e) => { document.body.insertAdjacentHTML('beforeend', `<p class="no">${esc(e.message)}</p>`); });
})();
</script>
</main></body></html>
"""
