using System.Collections.ObjectModel;
using NINA.Astrometry;
using NINA.Astrometry.Interfaces;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Conditions;
using NINA.Sequencer.Container;
using NINA.Sequencer.Container.ExecutionStrategy;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.Trigger;
using Newtonsoft.Json;

namespace Starfront.Collab.Sequencer {

    /// <summary>
    /// One dealt panel as a target container: the host's own Center, Switch
    /// Filter and Take Many Exposures instructions find their coordinates,
    /// position angle and target name here exactly as they would under a
    /// Deep Sky Object container, and the frames carry the panel's name in
    /// their metadata, which is how they are credited to it afterwards.
    ///
    /// Built by the collaboration container at run time from tonight's deal;
    /// never offered in the palette and never kept in a saved sequence.
    /// </summary>
    [JsonObject(MemberSerialization.OptIn)]
    public class StarfrontPanel : SequenceContainer, IDeepSkyObjectContainer {
        private readonly IProfileService profileService;
        private readonly INighttimeCalculator nighttimeCalculator;
        private InputTarget target;
        private string skipReason = "";

        public StarfrontPanel(IProfileService profileService, INighttimeCalculator nighttimeCalculator, PanelPlan plan) : base(new SequentialStrategy()) {
            this.profileService = profileService;
            this.nighttimeCalculator = nighttimeCalculator;
            Plan = plan;
            var astrometry = profileService.ActiveProfile.AstrometrySettings;
            var input = new InputTarget(Angle.ByDegree(astrometry.Latitude), Angle.ByDegree(astrometry.Longitude), astrometry.Horizon) {
                TargetName = plan.TargetName,
                InputCoordinates = new InputCoordinates(plan.Coordinates),
                PositionAngle = plan.Cell.Rotation,
            };
            Target = input;
            try { NighttimeData = nighttimeCalculator.Calculate(); } catch (Exception ex) { Logger.Debug($"Starfront: night data - {ex.Message}"); }
            Name = plan.Deal.Kind == "single" ? plan.Deal.ProjectName : $"{plan.Deal.ProjectName} · panel {plan.PanelNumber}";
            Category = "Starfront";
            Description = plan.SingleField ? "The field dealt to this telescope tonight" : "A panel dealt to this telescope tonight";
            Icon = Icons.Find("TelescopeSVG");
            IsExpanded = false;
        }

        public PanelPlan Plan { get; }

        [JsonProperty]
        public InputTarget Target {
            get => target;
            set { target = value; RaisePropertyChanged(); }
        }

        public NighttimeData NighttimeData { get; private set; }

        /// <summary>Set by the gate when the panel was not up when its turn came.</summary>
        public bool SkippedForAltitude { get; set; }

        public string SkipReason {
            get => skipReason;
            set { skipReason = value ?? ""; RaisePropertyChanged(); RaisePropertyChanged(nameof(Detail)); }
        }

        public string Detail {
            get {
                var where = $"{Plan.Coordinates.RAString} {Plan.Coordinates.DecString}";
                var text = $"{where} · {Plan.FramesText}";
                return SkipReason.Length > 0 ? $"{text} · {SkipReason}" : text;
            }
        }

        public override object Clone() {
            var copy = new StarfrontPanel(profileService, nighttimeCalculator, Plan);
            copy.CopyMetaData(this);
            foreach (var item in Items) { var child = (ISequenceItem)item.Clone(); copy.Items.Add(child); child.AttachNewParent(copy); }
            foreach (var condition in Conditions) { var child = (ISequenceCondition)condition.Clone(); copy.Conditions.Add(child); child.AttachNewParent(copy); }
            foreach (var trigger in Triggers) { var child = (ISequenceTrigger)trigger.Clone(); copy.Triggers.Add(child); child.AttachNewParent(copy); }
            return copy;
        }

        public override void ResetProgress() {
            base.ResetProgress();
            SkippedForAltitude = false;
            SkipReason = "";
        }

        public override string ToString() => $"Category: {Category}, Item: StarfrontPanel, Target: {Target?.TargetName} {Target?.InputCoordinates?.Coordinates}, {Plan.FramesText}";
    }
}
