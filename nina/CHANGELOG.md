# Changelog

## 1.0.0.9

- Every field recentres on its own coordinates when it drifts (the distance is an option; 0 switches it off). A *Center after drift* trigger placed on a container above the Starfront one takes its target from that container, and one run was slewed to M33 in the middle of M31 that way.
- A single-field collaboration no longer spends a slew and a plate solve measuring the camera's angle before starting, nor warns that the panels were laid for another angle; only a mosaic's grid depends on it.
- A run that is stopped still reports the frames shot since its last check-in.

## 1.0.0.8

- The container no longer fails the moment it runs. Its panels were added to the sequence from the sequencer's worker thread, which WPF refuses once the sequence editor is bound to them; N.I.N.A. then carried on with whatever instructions followed, and nothing was reported. Panels are now added and cleared on the window's thread.

## 1.0.0.7

- A sequence started again during the night carries on where it left off: frames already shot on a panel tonight are taken off its count, and a finished panel is not shot again.
- Frame times are no longer shifted when N.I.N.A. stamps them in local time.

## 1.0.0.6

- A camera without a rotator is plate-solved once at the first panel when its angle is unknown or differs from the angle the panels were laid for; the server then lays the panels again before the night starts, instead of a night being shot on a grid the camera cannot cover.
- Reported footprints carry the camera's own field size at the solved angle.

## 1.0.0.5

- Every collaboration on the Starfront panel shows its sky (DSS2 colour) with the region outlined and, once joined, your own panels drawn on it with tonight's share filled; cutouts are kept on disk.
- *Show in Framing Assistant* opens N.I.N.A.'s Framing Assistant on the region at its angle, with your camera's footprint.

## 1.0.0.4

- The Starfront panel on the Imaging tab lists every field of the night's run with where it has got to - done, shooting (frame n of N, or slewing, centring, changing filter), waiting, skipped and why - live, and keeps the last run until the next begins.

## 1.0.0.3

- Filters are shown everywhere as your own wheel names them; the server does the matching to its standard names.
- Wording covers single-field collaborations as well as mosaics.

## 1.0.0.2

- The server lives at collab.starfront.space; the old address, if typed in, is pointed at the new one.

## 1.0.0.1

- The container follows the server only while it sits in a sequence.

## 1.0.0.0

First closed-test release for the Starfront Observatories community.

- Sign in with Discord; only members of the Starfront server can take part.
- Per-filter exposure table; the server deals your share at your own sub lengths.
- *Starfront collaboration* sequence container: reads tonight's deal once, then slews, centres, switches filter and shoots each dealt panel, skipping panels below the project's altitude floor.
- Frames credited to their panel with star size and guiding error, reported on each check-in.
- Imaging-tab panel: who is online, open collaborations with compatibility and progress, tonight's deal.
