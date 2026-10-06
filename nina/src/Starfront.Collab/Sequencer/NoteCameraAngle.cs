using NINA.Astrometry;
using NINA.Core.Model;
using NINA.Core.Utility;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.SequenceItem.Platesolving;
using Newtonsoft.Json;
using Starfront.Collab.Server;
using Starfront.Collab.Services;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Sequencer {

    /// <summary>
    /// Reads the plate solve the centring just did. For a camera with no
    /// rotator, the solved position angle is the angle the camera really
    /// sits at, which the server needs to lay this telescope's panels along
    /// the camera's own axes; and the solved centre is where the panel was
    /// really shot, which is what its frames are credited to.
    /// </summary>
    [JsonObject(MemberSerialization.OptIn)]
    public class NoteCameraAngle : SequenceItem {
        private readonly Center center;
        private readonly StarfrontPanel panel;
        private readonly HostServices host;
        private readonly StarfrontSettings settings;
        private readonly FrameReporter reporter;
        private string detail = "";

        public NoteCameraAngle(Center center, StarfrontPanel panel, HostServices host, StarfrontSettings settings, FrameReporter reporter) {
            this.center = center;
            this.panel = panel;
            this.host = host;
            this.settings = settings;
            this.reporter = reporter;
            Name = "Starfront: note the solved angle";
            Category = "Starfront";
            Description = "Remembers the camera's position angle and the solved centre from the centring before it.";
            Icon = Icons.Find("PlatesolveSVG");
        }

        public string Detail {
            get => detail;
            private set { detail = value ?? ""; RaisePropertyChanged(); }
        }

        public override object Clone() => new NoteCameraAngle(center, panel, host, settings, reporter);

        public override Task Execute(IProgress<ApplicationStatus> progress, CancellationToken token) {
            var result = center?.PlateSolveStatusVM?.PlateSolveResult;
            if (result == null || !result.Success) {
                Detail = "no solve to read";
                return Task.CompletedTask;
            }
            var angle = result.PositionAngle;
            if (!RigProfile.HasRotator(host)) {
                var was = settings.CameraAngle;
                if (double.IsNaN(was) || Math.Abs(((angle - was + 180.0) % 360.0 + 360.0) % 360.0 - 180.0) > 0.05) {
                    settings.CameraAngle = Math.Round(angle, 2);
                    Logger.Info($"Starfront: camera sits at {angle:0.0}°" + (double.IsNaN(was) ? "" : $" (was {was:0.0}°)"));
                }
                var laidFor = panel.Plan.Cell.Rotation;
                if (RigProfile.HalfTurnDifference(angle, laidFor) > 2.0) {
                    Logger.Warning($"Starfront: this panel was laid for a camera at {laidFor:0.0}° and the camera sits at {angle:0.0}°; the server lays the panels again from the next check-in, and tomorrow's deal uses them");
                }
            }
            var context = reporter.Lookup(panel.Plan.TargetName);
            if (context != null && result.Coordinates != null) {
                var solved = result.Coordinates.Transform(Epoch.J2000);
                reporter.Register(new PanelContext {
                    TargetName = context.TargetName, ProjectId = context.ProjectId, ProjectName = context.ProjectName,
                    TaskId = context.TaskId, PanelNumber = context.PanelNumber, Filter = context.Filter,
                    Scale = context.Scale, FocalLength = context.FocalLength, Colour = context.Colour, Bandpass = context.Bandpass,
                    // Where the frame really landed and how it really sits: the
                    // solved centre and angle, with the camera's own field.
                    Footprint = new Region {
                        Ra = Math.Round(solved.RADegrees, 5), Dec = Math.Round(solved.Dec, 5),
                        Width = context.Footprint?.Width ?? panel.Plan.Cell.Width,
                        Height = context.Footprint?.Height ?? panel.Plan.Cell.Height,
                        Rotation = Math.Round(angle, 2),
                    },
                });
            }
            Detail = $"camera at {angle:0.0}°";
            return Task.CompletedTask;
        }

        public override string ToString() => $"Category: {Category}, Item: NoteCameraAngle, {Detail}";
    }
}
