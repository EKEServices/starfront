---
title: "Distributed live stacking: follow-up items"
subtitle: "For the author's review, morning of 2026-10-23"
---

# Where things stand

**Every code task is merged and pushed.** The task list is at 71 of 77. The six open tasks are your manual runs on the home network,
set out step by step in `STARFRONT_DISTRIBUTED_STACKING_CHECKLIST.pdf` beside this file.

| Repository | Branch and commit | CI on that commit |
|---|---|---|
| Starfront | `main` `5a6b5ce` | Python checks pass on Ubuntu and Windows (first green run since the workflow was added) |
| Rapid Stacker Collab | `master` `419b599` | backend-ci and contract-ci pass; **worker-ci was still running when this was written** (its Windows job takes about 1 h 40 min; it passed on the same code in pull request #22) |

Nothing was deployed, released or signed. No AWS resource was touched.

# Do first this morning

1. **Check Collab's worker-ci on master.** Actions, worker-ci, the run for `419b599`. If it is red, the failing job's log is the
   place to start; the same code passed every job on PR #22.
2. **Install Starfront's Python requirements on the Mac.** Your Anaconda `python3` lacks `python-multipart`; without it the local
   server refuses every result the hub sends back. `local_night.py` now stops at once and says so.

   ```bash
   cd ~/Develop/EKEServices/Astro/starfront && python3 -m pip install -r requirements.txt
   ```

3. **Start the checklist** (`STARFRONT_DISTRIBUTED_STACKING_CHECKLIST.pdf`), Part 0 then Part 1.

# Decisions for you

Each is small. My recommendation comes first in each row.

| # | Item | What it means today | Recommendation |
|---|---|---|---|
| 1 | **The release signing key** | A placeholder. Every desktop install and every worker self-update is refused by design. T507 cannot pass without it. | Generate it (`RSC/docs/runbooks/releasing-workers.md` §2), keep the private half offline, and paste the public half into `volunteer.py` and `self_update.rs`. Only you can do this. |
| 2 | **The hub's mirror of the worker manifest** (plan 4.6) | Never became a task, so it was never built. Volunteers install from the Starfront server instead of the hub. A LAN with no internet cannot install volunteers. | Build it: a small hub task (fetch `latest.json` and `.sig` hourly, serve them byte for byte at `/hub/v1/worker-manifest`). The desktop already tries it first. |
| 3 | **A desktop field for a collaboration's Discord webhook** | The server stores and uses `discordWebhookUrl`, but no screen sets it. Today only `local_night.py --discord-webhook` or the API does. | Add an owner-only field to the Collab tab's collaboration form. |
| 4 | **`stackContract` in the Starfront server's health** (plan 7.3) | Not built. Nothing reports which contract the server's front door speaks. | Add it; a few lines and a check case. |
| 5 | **Deleting a cloud-bound collaboration** | Collab's project delete and restore routes accept only a signed-in web session, so Starfront can only *pause* the cloud project. | Either accept pausing, or add a token-scoped owner delete to Collab's API (a contract change, then an AWS deploy, which is yours). Accepting is the cheaper choice until a real cloud collaboration needs deleting. |
| 6 | **Purge timing for deleted hub projects** | The purge follows the seven-day window by up to a day, because retention runs daily. Documented in the hub runbook. | Accept it; it errs on the side of keeping data. |
| 7 | **T310's wording** | It says "standalone hub", but projects now only come from Starfront (your direction). The checklist runs it through the local server. | Let me reword the task to match. |

# What was found and fixed overnight

You may want to know about these before you test. The first would have broken the deployed server.

- **The Starfront server refused every hub push.** `python-multipart` was in neither `requirements.txt` nor
  `server/deploy/install.sh`, so `request.form()` failed and the server answered 400. No verdict, preview or master could ever
  reach the pages. Fixed in `28ac374`; **the next server deploy installs it** (install.sh's pip line now includes it).
- **Part uploads were corrupted on Windows.** The server wrote parts in text mode, which turns every newline byte into two.
  Fixed in `9d0b6e4` (binary mode; no change on Linux, where the server runs).
- **CI was red in both repositories for most of the build**, and I had been watching only the plugin build. Starfront's Python
  checks had never passed. Collab's worker, backend and contract workflows broke during the build. All are fixed:
  - real Windows bugs in the hub and worker (free disk space unreadable on Windows; a directory under `/public` answered 500
    instead of 404; macOS and Linux service files written with Windows separators);
  - one worker setting missing from the Terraform name list;
  - the contract check now builds the hub on the runner;
  - the hub end-to-end script gives its workers a scratch cap, so GitHub's small runners pass the 20 GB rule. The rule for real
    volunteers is unchanged.
- **`local_night.py` could not take real frames.** It now always joins its telescope, saves the 24-hour grant, prints the
  uploader commands, and takes `--region` and `--discord-webhook`. It also refuses to start without the server's requirements.
- **A worker race** (from the T308 run): after a restart two jobs shared one temporary catalogue file and one stalled for
  30 minutes. Fixed by a unique temporary name.

# Release and deploy steps that stay yours

None of these is needed for the home-network tests.

- **Starfront server deploy** (`Deploy the Starfront server.cmd`): ships the hub API, the `/stack` pages and the front door. It
  runs `install.sh`, which now installs `python-multipart`.
- **Starfront desktop installer** (`packaging\build.ps1`) and **plugin release**, after T507 and T606 pass.
- **Collab worker and hub release**: the worker-release workflow, then sign the manifests with your key (item 1) and copy them
  into `server/downloads/`.
- **Collab's AWS stack** needs nothing for the hub. Its Terraform changed only in the worker-config name list; apply it whenever
  you next deploy.

# Housekeeping

- **Unversioned files changed:** the suite guides `Astro/Rust/CLAUDE.md` and `AGENTS.md` now say contract 1.28.0 and describe the
  hub. `Astro/Running-the-servers-locally.md` points to the new runbook and `local_night.py`.
- **Your main Collab checkout** still holds a built Lambda tree from the last deploy, so the local-stack tests fail there. Run them
  in a worktree, or run `backend/scripts/clean.sh` when no deploy is pending.
- **Branches:** every task branch is merged. Starfront's `upstream` remote has a `cursor/collab-live-stack-23be` branch that this
  work did not create or touch.
- **Progress page:** https://claude.ai/artifact/WJummaRZgi5Z3v6asiT9MX (refreshed when anything lands).
