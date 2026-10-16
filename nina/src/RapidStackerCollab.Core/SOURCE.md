# Where this folder comes from

`RapidStackerCollab.Core` is the platform-independent core of the Rapid Stacker Collab N.I.N.A. plugin
(EKE Services LLC, MPL-2.0), copied here so the Starfront plugin can upload each saved light to a Rapid
Stacker Collab project for live stacking. It has no N.I.N.A., WPF or Windows dependency.

| | |
|---|---|
| Upstream repository | `github.com/EKE-Services/rapid-stacker-collab` (private; the plugin folder is to be mirrored publicly) |
| Upstream path | `nina-plugin/src/RapidStackerCollab.Core/` and `nina-plugin/tests/RapidStackerCollab.Core.Tests/` |
| Core copied from | commit `dab8b3b425d33cb30c12013644d37433531e08a8` (`master`, 2026-10-16) |
| Tests copied from | commit `5047a00df519caed44b55cb2c92a0ede408004bf` (branch `fix/plugin-test-teardown-retry`: `master` plus the Windows temp-directory deletion retry in the tests; the Core sources are identical on both) |

## Rules

- **No local edits** to the copied `.cs` files, `aliases.json` or the csproj files. A change Starfront needs goes upstream first and is
  copied back; a change that cannot wait is recorded below under "Local differences" with the reason, so a re-sync carries it.
- The copy is built to upstream's rules through the `Directory.Build.props` and `.editorconfig` beside it (nullable on, warnings
  as errors, MPL header on every file), not Starfront's.
- Tests that read the upstream repository's contract (`docs/api/openapi.yaml`, `docs/api/examples/strip-vectors.json`,
  `scripts/contract-examples.json`, `worker/crates/filters/`) are skipped here with a reason (`Support/RepoPaths.cs`). Vendoring
  those files under `nina/docs/api/` and `nina/scripts/` would make them run with no code change.

## Re-syncing

```bash
RSC=/path/to/rapid-stacker-collab; REF=master
git -C "$RSC" archive "$REF" nina-plugin/src/RapidStackerCollab.Core | tar -x -C src --strip-components=2
git -C "$RSC" archive "$REF" nina-plugin/tests/RapidStackerCollab.Core.Tests | tar -x -C tests --strip-components=2
```

Then restore this file, the two `Directory.Build.props` and the two `.editorconfig` files (`git checkout -- ...`), update the
commits in the table, build, run the tests, and diff the ported files below against their upstream sources.

## Local differences

None.

## Ported by hand, not copied

The Starfront plugin does not use upstream's Shell or Plugin projects (their settings, logging and MVVM worlds duplicate
Starfront's own). These upstream files were ported into `src/Starfront.Collab/Services/Stacking/` in Starfront's style and must
be diffed against upstream on a re-sync:

| Starfront file | Upstream source |
|---|---|
| `StackingUploader.cs` | `RapidStackerCollab.Shell/Hosting/CollabServices.cs` (the object graph and lifetime) |
| `StackingIntake.cs` | `RapidStackerCollab.Shell/Intake/FrameIntake.cs` (without attribution) |
| `StackingMapping.cs` | `RapidStackerCollab.Shell/Filters/FilterMappingGrid.cs` (the prefill rules) |
| `JoinCodes.cs` | `RapidStackerCollab.Shell/Projects/JoinFlow.cs` (the code normaliser) |
| `ImagingState.cs` | `RapidStackerCollab.Shell/Imaging/ImagingStateTracker.cs`, `RapidStackerCollab.Plugin/Services/NinaAdapters.cs` (`CameraStateConsumer`) |
| `VerdictPoller.cs` | `RapidStackerCollab.Shell/Dock/VerdictPoller.cs` |
| `TempCleaner.cs` | `RapidStackerCollab.Shell/Hosting/TempCleaner.cs` |
| `NativeSqlite.cs` | `RapidStackerCollab.Plugin/Services/NinaAdapters.cs` (`NativeSqlite.Preload`) |

(The table lists the files the live-stacking work adds in its later phases; a row whose file does not exist yet is planned, not missing.)
