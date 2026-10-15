# Working on Starfront

Starfront is open source under the Mozilla Public License 2.0 (`LICENSE`).
The people it is built for are the ones running it on their own telescopes,
so the best contributions come from a night that went wrong and the fix that
would have saved it.

## The lay of the land

| | |
|---|---|
| `astrocontrol/` | the program: devices, sequencer, planner, image pipeline, the web interface under `astrocontrol/web/` |
| `server/` | the collaboration server at collab.starfront.space: who is on the sky, what each telescope is dealt, the ledger, the files |
| `nina/` | the N.I.N.A. plugin (.NET 8), with its own README and build steps |
| `tools/` | the checks: `python tools/check_<thing>.py`, no test framework, each one a story of a fault that happened |
| `docs/` | the guides people read, as HTML rendered to PDF |

`README.md` is long on purpose: every design decision in the program is
written down next to the reason it was made, usually a night that was lost
without it. Read the section for the part you are changing before changing it.

## Running it from source

```
pip install -r requirements.txt
python run.py
```

Python 3.12 or later on Windows (ASCOM is COM). `python run.py --browser` runs
without the window. The server runs with `python server/run.py` and talks to a
local Starfront out of the box.

## Before opening a pull request

1. Run the checks that cover what you touched. `python tools/check_server.py`
   for the server, `check_night.py` and `check_recovery.py` for the sequencer,
   `check_controls.py` whenever the web interface changed. They print
   `PASS`/`FAIL` lines and a count at the end; all of them should pass before
   and after.
2. When you fix a fault, add a case that fails on the old code. The checks are
   the record of what went wrong once and must not again.
3. Say in the commit message what went wrong and why the change is the fix,
   not only what the change is. The code base is written that way throughout.
4. Keep units honest: star sizes in arcseconds, never pixels; right ascension
   in degrees on the server and hours in the program; times as a *time of
   night* where a person chose one.

## What not to commit

Settings, targets, plans, logs, frames, databases and `discord.env` are all in
`.gitignore` and must stay out: they belong to an observatory, not the code.
Never put a token, a password or the Discord client secret in a file, a test
or a commit message. The checks make their own fake Discord and fake server.

## Where to talk

The Starfront Discord server, which is also where collaborations are run.
Issues and pull requests on GitHub are fine too.
