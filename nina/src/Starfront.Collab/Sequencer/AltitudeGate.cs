using NINA.Core.Enum;
using NINA.Core.Model;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using NINA.Sequencer.SequenceItem;
using Newtonsoft.Json;
using Starfront.Collab.Services;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Sequencer {

    /// <summary>
    /// First thing in every panel: is it up? If the panel is below the
    /// project's altitude floor, below the profile's horizon, or too close to
    /// a Moon that is up, the rest of the panel is skipped for now and the
    /// collaboration container comes back to it after the others.
    /// </summary>
    [JsonObject(MemberSerialization.OptIn)]
    public class AltitudeGate : SequenceItem {
        private readonly StarfrontPanel panel;
        private readonly IProfileService profileService;
        private readonly RunState run;
        private string detail = "";

        public AltitudeGate(StarfrontPanel panel, IProfileService profileService, RunState run) {
            this.panel = panel;
            this.profileService = profileService;
            this.run = run;
            Name = "Starfront: is the panel up?";
            Category = "Starfront";
            Description = "Skips the panel for now if it is below the project's floor, the horizon, or too near the Moon.";
            Icon = Icons.Find("WaitForAltitudeSVG");
            Detail = panel.Plan.MinAltitude > 0 ? $"floor {panel.Plan.MinAltitude:0}°" : "above the horizon";
        }

        public string Detail {
            get => detail;
            private set { detail = value ?? ""; RaisePropertyChanged(); }
        }

        public override object Clone() => new AltitudeGate(panel, profileService, run) { Detail = Detail };

        public override Task Execute(IProgress<ApplicationStatus> progress, CancellationToken token) {
            var plan = panel.Plan;
            var (altitude, azimuth) = SkyMath.AltAz(plan.Coordinates, profileService);
            var floor = Math.Max(plan.MinAltitude, SkyMath.HorizonAltitude(profileService, azimuth));
            string reason = null;
            if (altitude < floor) {
                reason = $"at {altitude:0}°, below the {floor:0}° floor";
            } else if (plan.MinMoonSeparation is double wanted && wanted > 0) {
                var (separation, moonUp) = SkyMath.MoonSeparation(plan.Coordinates, profileService);
                if (moonUp && separation < wanted) reason = $"Moon {separation:0}° away, project wants {wanted:0}°";
            }
            if (reason != null) {
                panel.SkippedForAltitude = true;
                panel.SkipReason = $"skipped for now: {reason}";
                Detail = panel.SkipReason;
                Logger.Info($"Starfront: {panel.Name} {panel.SkipReason}");
                progress?.Report(new ApplicationStatus { Status = $"Starfront: {panel.Name} {panel.SkipReason}" });
                foreach (var sibling in panel.GetItemsSnapshot()) {
                    if (!ReferenceEquals(sibling, this) && sibling.Status == SequenceEntityStatus.CREATED) sibling.Skip();
                }
                return Task.CompletedTask;
            }
            panel.SkippedForAltitude = false;
            panel.SkipReason = "";
            Detail = $"up at {altitude:0}°";
            run.Running = true;
            run.TargetName = plan.TargetName;
            run.ProjectId = plan.Deal.Project;
            Logger.Info($"Starfront: starting {panel.Name} at {altitude:0}° altitude - {plan.FramesText}");
            return Task.CompletedTask;
        }

        public override string ToString() => $"Category: {Category}, Item: AltitudeGate, {Detail}";
    }
}
