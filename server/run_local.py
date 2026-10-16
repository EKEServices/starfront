#!/usr/bin/env python3
"""Run the collaboration server on this machine, with a stand-in for Discord.

    python server/run_local.py

For trying the server, the Starfront program and the N.I.N.A. plugin out
locally. The real server signs people in through Discord, which needs a
Discord application with a client secret and a redirect Discord will accept.
This runs the same server with a small Discord of its own: the sign-in page
asks only *which name*, and anyone who presses Continue is signed in as that
person. The name given with --name (ncedelliott unless told otherwise) owns
the server, so the first sign-in can do everything; any other name typed on
that page is an ordinary member, for seeing what somebody else's telescope
sees.

    --name NAME     the owner's Discord name (default ncedelliott)
    --port N        listen port (default 8800)
    --data DIR      where the database and token live (default ~/starfront-local)

The stand-in is served by the server itself, under /_local/discord, so there
is one address to reach: an SSH tunnel to 8800 is all another PC needs.

**Never on a network, never on a real server's data.** The stand-in signs in
whoever asks, as the owner if they like, so it listens on 127.0.0.1 and there
is no --host. A folder with a discord.env in it, or the default folder a real
server would use, is refused: signing in to a real database through a door
with no lock is exactly the mistake this must not make easy. Discord settings
already in the environment (a shell that has the real secret exported) are
overridden, never mixed in.
"""

# No `from __future__ import annotations`: FastAPI reads the stand-in's
# parameter types at runtime, and `Request` is imported inside the function.
import argparse
import hashlib
import html
import os
import re
import secrets
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Where the stand-in Discord lives, on the server's own address.
PREFIX = "/_local/discord"

#: The guild every stand-in person is a member of.
GUILD = "local"

#: What a Discord username may be; checked before anything is echoed back.
_NAME = re.compile(r"^[a-z0-9_.]{2,32}$")


def person_id(name: str) -> str:
    """A Discord-like id that is the same for the same name every run, so a
    restart finds the same person (and their projects) in the database."""
    digest = int(hashlib.sha256(name.encode()).hexdigest()[:15], 16)
    return str(900_000_000_000_000_000 + digest % 10**17)


def stand_in_discord(secret: str, callback: str, owner: str):
    """Enough of Discord for a sign-in: the authorize page, the code exchange,
    who-am-I and the guild membership lookup (server/auth.py's three calls)."""
    from fastapi import APIRouter, HTTPException, Header, Query, Request
    from fastapi.responses import HTMLResponse, RedirectResponse
    from server import auth

    router = APIRouter(prefix=PREFIX)
    codes: dict[str, str] = {}       # authorization code -> name, used once
    bearers: dict[str, str] = {}     # access token -> name

    def back(redirect_uri: str, **query: str) -> RedirectResponse:
        # Only ever back to this server's own callback: a stand-in that sent
        # people wherever the query said would be an open redirect.
        if redirect_uri != callback:
            raise HTTPException(status_code=400, detail="unknown redirect_uri")
        return RedirectResponse(f"{callback}?{urllib.parse.urlencode(query)}",
                                status_code=303)

    @router.get("/oauth2/authorize")
    async def authorize(redirect_uri: str = Query(default=""),
                        state: str = Query(default="", max_length=64)):
        if redirect_uri != callback:
            raise HTTPException(status_code=400, detail="unknown redirect_uri")
        hidden = (f'<input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">'
                  f'<input type="hidden" name="state" value="{html.escape(state)}">')
        body = (
            "This is the stand-in for Discord that <code>server/run_local.py</code> runs. "
            f"<b>@{html.escape(owner)}</b> owns this server; any other name is an "
            "ordinary member.</p>"
            f'<form action="{PREFIX}/oauth2/approve" method="get">{hidden}'
            f'<p><input name="name" value="{html.escape(owner)}" size="20" '
            'pattern="[a-z0-9_.]{2,32}" autofocus> '
            '<button type="submit">Continue</button></p></form>'
            f'<form action="{PREFIX}/oauth2/deny" method="get">{hidden}'
            '<p><button type="submit">Cancel</button>')
        return HTMLResponse(auth.page("Sign in to Starfront (local)", body))

    @router.get("/oauth2/approve")
    async def approve(redirect_uri: str = Query(default=""),
                      state: str = Query(default="", max_length=64),
                      name: str = Query(default="")):
        name = name.strip().lstrip("@").lower()
        if not _NAME.match(name):
            raise HTTPException(status_code=400,
                                detail="a name is 2 to 32 of a-z, 0-9, _ and .")
        code = secrets.token_urlsafe(16)
        codes[code] = name
        return back(redirect_uri, code=code, state=state)

    @router.get("/oauth2/deny")
    async def deny(redirect_uri: str = Query(default=""),
                   state: str = Query(default="", max_length=64)):
        return back(redirect_uri, error="access_denied", state=state)

    @router.post("/oauth2/token")
    async def token(request: Request):
        form = dict(urllib.parse.parse_qsl((await request.body()).decode()))
        name = codes.pop(form.get("code", ""), None)
        if form.get("client_secret") != secret or name is None \
                or form.get("redirect_uri") != callback:
            raise HTTPException(status_code=400, detail="invalid_grant")
        bearer = secrets.token_urlsafe(24)
        bearers[bearer] = name
        return {"access_token": bearer, "token_type": "Bearer"}

    def who(authorization: str) -> str:
        name = bearers.get(authorization.removeprefix("Bearer ").strip())
        if name is None:
            raise HTTPException(status_code=401, detail="401: Unauthorized")
        return name

    @router.get("/users/@me")
    async def me(authorization: str = Header(default="")):
        name = who(authorization)
        return {"id": person_id(name), "username": name, "global_name": None,
                "avatar": ""}

    @router.get("/users/@me/guilds/{guild}/member")
    async def member(guild: str, authorization: str = Header(default="")):
        who(authorization)
        if guild != GUILD:
            raise HTTPException(status_code=404, detail="Unknown Guild")
        return {"roles": [], "nick": None}

    return router


def refuse(message: str) -> None:
    print(f"run_local: {message}", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Starfront collaboration server, locally, with a stand-in Discord")
    parser.add_argument("--name", default="ncedelliott",
                        help="the owner's Discord name (default ncedelliott)")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--data", default=str(Path.home() / "starfront-local"),
                        help="data folder (default ~/starfront-local)")
    args = parser.parse_args(argv)

    owner = args.name.strip().lstrip("@").lower()
    if not _NAME.match(owner):
        refuse("--name is 2 to 32 of a-z, 0-9, _ and .")

    data = Path(args.data).expanduser().resolve()
    from server import auth
    if (data / auth.ENV_FILE).exists():
        refuse(f"{data} has a {auth.ENV_FILE}, so it is a real server's folder."
               " Give --data a folder of its own.")
    from astrocontrol.config import data_root
    if data == (data_root() / "collab").resolve():
        refuse(f"{data} is where a real server keeps its database."
               " Give --data a folder of its own.")
    data.mkdir(parents=True, exist_ok=True)

    base = f"http://127.0.0.1:{args.port}"
    secret = secrets.token_urlsafe(24)
    # Whatever the shell had, under either name, gives way: a real client
    # secret or guild must not be mixed with the stand-in.
    for key in list(os.environ):
        if key.startswith(("STARFRONT_DISCORD_", "ASTROCOLLAB_DISCORD_",
                           "ASTROCOLLAB_PUBLIC_URL", "ASTROCOLLAB_DATA")):
            del os.environ[key]
    os.environ.update({
        "STARFRONT_DATA": str(data),
        "STARFRONT_PUBLIC_URL": base,
        "STARFRONT_DISCORD_CLIENT_ID": "starfront-local",
        "STARFRONT_DISCORD_CLIENT_SECRET": secret,
        "STARFRONT_DISCORD_GUILD": GUILD,
        "STARFRONT_DISCORD_OWNERS": person_id(owner),
        "STARFRONT_DISCORD_API": base + PREFIX,
        "STARFRONT_DISCORD_AUTHORIZE": base + PREFIX + "/oauth2/authorize",
    })

    # The server reads its settings when it is imported, so only now; the
    # routes are added to the same app object run.py then serves.
    from server import app as server_app
    server_app.app.include_router(stand_in_discord(
        secret, base + "/auth/discord/callback", owner))

    print(f"Stand-in Discord at {base}{PREFIX}: signing in asks only for a name.")
    print(f"  @{owner} owns this server; any other name is an ordinary member.")
    print("  Local use only: anyone who can reach this port can sign in as anyone.\n")
    from server import run
    run.main(["--host", "127.0.0.1", "--port", str(args.port)])


if __name__ == "__main__":
    main()
