using System.ComponentModel.Composition;
using System.IO;
using System.Runtime.Serialization;
using NINA.Astrometry;
using NINA.Astrometry.Interfaces;
using NINA.Core.Enum;
using NINA.Core.Locale;
using NINA.Core.Model;
using NINA.Core.Model.Equipment;
using NINA.Core.Utility;
using NINA.Core.Utility.Notification;
using NINA.Core.Utility.WindowService;
using NINA.Equipment.Interfaces;
using NINA.Equipment.Interfaces.Mediator;
using NINA.PlateSolving.Interfaces;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Conditions;
using NINA.Sequencer.Container;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.SequenceItem.FilterWheel;
using NINA.Sequencer.SequenceItem.Imaging;
using NINA.Sequencer.SequenceItem.Platesolving;
using NINA.Sequencer.Trigger;
using NINA.Sequencer.Trigger.Guider;
using NINA.WPF.Base.Interfaces.Mediator;
using NINA.WPF.Base.Interfaces.ViewModel;
using Newtonsoft.Json;
using Starfront.Collab.Server;
using Starfront.Collab.Services;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Sequencer {

    /// <summary>A collaboration to choose in the container's editor.</summary>
    public sealed record ProjectChoice(string Id, string Name);

    /// <summary>
    /// The one instruction a N.I.N.A. user needs: drop it where a target
    /// would go, inside the autofocus, meridian-flip and dither triggers they
    /// already use. When it runs it asks the server what this telescope was
    /// dealt tonight - once; a night's deal holds for the night - and builds
    /// a panel container per dealt panel: is it up, centre (and rotate, with
    /// a rotator), switch filter, take the frames. The host's own
    /// instructions do the work, so everything around them behaves as it
    /// would around a target, and every light is credited to its panel.
    /// </summary>
    [ExportMetadata("Name", "Starfront collaboration")]
    [ExportMetadata("Description", "Shoots the panels and filter the Starfront server dealt this telescope tonight, at your own exposures.")]
    [ExportMetadata("Icon", "Starfront_CollabSVG")]
    [ExportMetadata("Category", "Starfront")]
    [Export(typeof(ISequenceItem))]
    [Export(typeof(ISequenceContainer))]
    [JsonObject(MemberSerialization.OptIn)]
    public class StarfrontCollaborationContainer : SequenceContainer {
        private readonly IProfileService profileService;
        private readonly ICameraMediator cameraMediator;
        private readonly ITelescopeMediator telescopeMediator;
        private readonly IImagingMediator imagingMediator;
        private readonly IImageSaveMediator imageSaveMediator;
        private readonly IImageHistoryVM imageHistoryVM;
        private readonly IFilterWheelMediator filterWheelMediator;
        private readonly IGuiderMediator guiderMediator;
        private readonly IRotatorMediator rotatorMediator;
        private readonly IDomeMediator domeMediator;
        private readonly IDomeFollower domeFollower;
        private readonly IPlateSolverFactory plateSolverFactory;
        private readonly IWindowServiceFactory windowServiceFactory;
        private readonly INighttimeCalculator nighttimeCalculator;

        private string projectId = "";
        private string summary = "";
        private IList<string> issues = new List<string>();

        [ImportingConstructor]
        public StarfrontCollaborationContainer(IProfileService profileService,
                                               ICameraMediator cameraMediator,
                                               ITelescopeMediator telescopeMediator,
                                               IImagingMediator imagingMediator,
                                               IImageSaveMediator imageSaveMediator,
                                               IImageHistoryVM imageHistoryVM,
                                               IFilterWheelMediator filterWheelMediator,
                                               IGuiderMediator guiderMediator,
                                               IRotatorMediator rotatorMediator,
                                               IDomeMediator domeMediator,
                                               IDomeFollower domeFollower,
                                               IPlateSolverFactory plateSolverFactory,
                                               IWindowServiceFactory windowServiceFactory,
                                               INighttimeCalculator nighttimeCalculator) : base(new OnceStrategy()) {
            this.profileService = profileService;
            this.cameraMediator = cameraMediator;
            this.telescopeMediator = telescopeMediator;
            this.imagingMediator = imagingMediator;
            this.imageSaveMediator = imageSaveMediator;
            this.imageHistoryVM = imageHistoryVM;
            this.filterWheelMediator = filterWheelMediator;
            this.guiderMediator = guiderMediator;
            this.rotatorMediator = rotatorMediator;
            this.domeMediator = domeMediator;
            this.domeFollower = domeFollower;
            this.plateSolverFactory = plateSolverFactory;
            this.windowServiceFactory = windowServiceFactory;
            this.nighttimeCalculator = nighttimeCalculator;
            Runtime.Ensure(new HostServices(profileService, cameraMediator, telescopeMediator, filterWheelMediator,
                                            rotatorMediator, guiderMediator, imageSaveMediator, nighttimeCalculator));
            Runtime.Session.Updated += OnSessionUpdated;
        }

        private void OnSessionUpdated(object sender, EventArgs e) {
            Ui.Dispatch.OnUi(() => {
                RaisePropertyChanged(nameof(ProjectChoices));
                RaisePropertyChanged(nameof(TonightText));
            });
        }

        // -- what is saved -------------------------------------------------------

        /// <summary>One collaboration to shoot, or blank for every one this telescope has joined.</summary>
        [JsonProperty]
        public string ProjectId {
            get => projectId;
            set { projectId = value ?? ""; RaisePropertyChanged(); RaisePropertyChanged(nameof(TonightText)); }
        }

        [OnDeserialized]
        public void OnDeserializedStarfront(StreamingContext context) {
            // The panels are tonight's, built when the container runs. Ones
            // written into a saved sequence are last night's and are dropped.
            foreach (var item in Items.ToList()) {
                item.AttachNewParent(null);
                Items.Remove(item);
            }
        }

        // -- what is shown -------------------------------------------------------

        public string Summary {
            get => summary;
            private set { summary = value ?? ""; RaisePropertyChanged(); }
        }

        public new IList<string> Issues {
            get => issues;
            private set { issues = value; RaisePropertyChanged(); }
        }

        public IList<ProjectChoice> ProjectChoices {
            get {
                var choices = new List<ProjectChoice> { new("", "Every collaboration I have joined") };
                if (Runtime.Ready) {
                    foreach (var group in Runtime.Session.Tasks.Where(t => t.Live).GroupBy(t => t.Project)) {
                        choices.Add(new ProjectChoice(group.Key, group.First().ProjectName));
                    }
                }
                if (ProjectId.Length > 0 && choices.All(c => c.Id != ProjectId)) choices.Add(new ProjectChoice(ProjectId, $"{ProjectId} (not on this telescope's list yet)"));
                return choices;
            }
        }

        /// <summary>What the last check-in dealt, before the run reads it for real.</summary>
        public string TonightText {
            get {
                if (!Runtime.Ready) return "Starfront is not set up";
                if (!Runtime.Settings.Enrolled) return "Sign in with Discord under Options > Plugins > Starfront Collaboration";
                if (Status == SequenceEntityStatus.RUNNING && Summary.Length > 0) return Summary;
                var deals = Runtime.Session.DealTonight(ProjectId);
                if (deals.Count == 0) {
                    return Runtime.Session.Tasks.Count == 0
                        ? "No collaboration joined yet; join one on the Starfront panel of the Imaging tab"
                        : "Nothing dealt for tonight yet; the deal is read when the sequence runs";
                }
                return string.Join(" · ", deals.Select(d => $"{d.ProjectName}: {StarfrontSession.Describe(d)}"));
            }
        }

        // -- running -----------------------------------------------------------------

        public override async Task Execute(IProgress<ApplicationStatus> progress, CancellationToken token) {
            ClearRuntimeChildren();
            if (!Runtime.Ready || !Runtime.Settings.Enrolled) {
                throw new SequenceEntityFailedException("Starfront: sign in with Discord under Options > Plugins > Starfront Collaboration first");
            }
            var session = Runtime.Session;
            var settings = Runtime.Settings;
            var reporter = Runtime.Reporter;
            progress?.Report(new ApplicationStatus { Status = "Starfront: asking what to shoot tonight" });
            try {
                await session.PollAsync(token);
            } catch (ServerException ex) {
                if (session.DealTonight(ProjectId).Count == 0) {
                    throw new SequenceEntityFailedException($"Starfront: {ex.Message}");
                }
                Logger.Warning($"Starfront: {ex.Message}; shooting the deal from the last check-in");
            }
            var deals = session.DealTonight(ProjectId);
            var plans = PlanPanels(deals, settings);
            if (plans.Count == 0) {
                Summary = deals.Count == 0 ? "Nothing dealt to this telescope tonight" : "The deal has no frames for this telescope tonight";
                Logger.Info($"Starfront: {Summary}");
                Notification.ShowInformation($"Starfront: {Summary}");
                return;
            }
            Order(plans);
            foreach (var plan in plans) Add(BuildPanel(plan, settings, reporter));
            var totalHours = plans.Sum(p => p.Seconds) / 3600.0;
            Summary = $"{string.Join(" · ", deals.Select(d => $"{d.ProjectName}: {StarfrontSession.Describe(d)}"))} · about {totalHours:0.0} h";
            RaisePropertyChanged(nameof(TonightText));
            Logger.Info($"Starfront: tonight - {Summary}");
            foreach (var plan in plans) Logger.Info($"Starfront:   panel {plan.PanelNumber} of {plan.Deal.ProjectName}: {plan.FramesText}"
                                                    + (plan.HoursUntilUp is double wait && wait > 0 ? $" (up in {wait:0.0} h)" : ""));

            session.Run.Running = true;
            session.Run.Summary = Summary;
            try {
                await base.Execute(progress, token);
                // Panels that were not up when their turn came get another
                // turn once the rest are done, while any has risen.
                for (var pass = 0; pass < 2; pass++) {
                    token.ThrowIfCancellationRequested();
                    var again = Items.OfType<StarfrontPanel>().Where(p => p.SkippedForAltitude && IsUpNow(p.Plan)).ToList();
                    if (again.Count == 0) break;
                    Logger.Info($"Starfront: coming back to {again.Count} panel(s) that have risen");
                    foreach (var panel in again) panel.ResetAll();
                    await base.Execute(progress, token);
                }
            } finally {
                session.Run.Running = false;
                session.Run.TargetName = null;
                session.Run.ProjectId = null;
                var done = Items.OfType<StarfrontPanel>().Count(p => p.Status == SequenceEntityStatus.FINISHED && !p.SkippedForAltitude);
                var skipped = Items.OfType<StarfrontPanel>().Count(p => p.SkippedForAltitude);
                Summary = $"{done} of {plans.Count} panels shot" + (skipped > 0 ? $", {skipped} never rose above the floor" : "");
                Logger.Info($"Starfront: {Summary}");
                try { await session.ReportPendingAsync(token); } catch (Exception ex) { StarfrontClient.Log("reporting the night", ex); }
            }
        }

        private bool IsUpNow(PanelPlan plan) {
            var (altitude, azimuth) = SkyMath.AltAz(plan.Coordinates, profileService);
            return altitude >= Math.Max(plan.MinAltitude, SkyMath.HorizonAltitude(profileService, azimuth));
        }

        private void ClearRuntimeChildren() {
            foreach (var item in Items.ToList()) {
                if (item is StarfrontPanel panel) Runtime.Reporter?.Forget(panel.Plan.TargetName);
                item.AttachNewParent(null);
                Items.Remove(item);
            }
            Summary = "";
        }

        /// <summary>Tonight's deal as panels: which cells, which filters, how many frames at which exposure.</summary>
        private List<PanelPlan> PlanPanels(List<DealTask> deals, Settings.StarfrontSettings settings) {
            var plans = new List<PanelPlan>();
            var wheel = settings.ProfileFilterNames();
            foreach (var deal in deals) {
                var wants = Runtime.Session.RequirementsFor(deal.Project);
                var frames = (deal.Visit?.Frames ?? new Dictionary<string, int>()).Where(kv => kv.Value > 0).ToList();
                if (frames.Count == 0) continue;
                var filters = new List<FilterFrames>();
                foreach (var (name, count) in frames) {
                    var canonical = Filters.Canonical(name);
                    var exposure = deal.ExposureFor(name);
                    if (exposure <= 0) exposure = settings.ExposureFor(name) ?? 0.0;
                    if (exposure <= 0) {
                        Logger.Warning($"Starfront: no exposure known for {Filters.Label(canonical)} on {deal.ProjectName}; set one in the exposure table");
                        continue;
                    }
                    var wheelName = wheel.FirstOrDefault(w => Filters.Same(w, name));
                    if (wheel.Count > 0 && wheelName == null && !settings.Colour) {
                        Logger.Warning($"Starfront: this profile's filter wheel has no {Filters.Label(canonical)}; {deal.ProjectName} dealt it anyway and it is skipped");
                        continue;
                    }
                    filters.Add(new FilterFrames(canonical, wheelName, count, exposure));
                }
                if (filters.Count == 0) continue;
                foreach (var index in deal.Share.Distinct()) {
                    if (index < 0 || index >= deal.Cells.Count) continue;
                    var cell = deal.Cells[index];
                    var coordinates = new Coordinates(Angle.ByDegree(cell.Ra), Angle.ByDegree(cell.Dec), Epoch.J2000);
                    var targetName = deal.Kind == "single" ? Ascii(deal.ProjectName) : $"{Ascii(deal.ProjectName)} panel {index + 1}";
                    plans.Add(new PanelPlan {
                        Deal = deal, CellIndex = index, Cell = cell, Coordinates = coordinates, Filters = filters,
                        MinAltitude = wants?.MinAltitude ?? 0.0, MinMoonSeparation = wants?.MinMoonSeparation,
                        TargetName = targetName,
                    });
                }
            }
            return plans;
        }

        private static string Ascii(string text) {
            var clean = new string((text ?? "").Where(c => c < 128 && !Path.GetInvalidFileNameChars().Contains(c)).ToArray()).Trim();
            return clean.Length > 0 ? clean : "Starfront";
        }

        /// <summary>
        /// Panels that are up go first, the one that sets soonest at the
        /// front; panels still to rise follow in the order they rise. A panel
        /// that never clears the floor in the next twelve hours is dropped
        /// here rather than slewed to and skipped.
        /// </summary>
        private void Order(List<PanelPlan> plans) {
            var now = DateTime.Now;
            foreach (var plan in plans) {
                double? up = null, down = null;
                for (var minutes = 0; minutes <= 12 * 60; minutes += 10) {
                    var at = now.AddMinutes(minutes);
                    var (altitude, azimuth) = SkyMath.AltAz(plan.Coordinates, profileService, at);
                    var floor = Math.Max(plan.MinAltitude, SkyMath.HorizonAltitude(profileService, azimuth));
                    var above = altitude >= floor;
                    if (above && up == null) up = minutes / 60.0;
                    if (!above && up != null && down == null) { down = minutes / 60.0; break; }
                }
                plan.HoursUntilUp = up;
                plan.HoursAbove = up.HasValue ? (down ?? 12.0) : null;
            }
            var never = plans.Where(p => p.HoursUntilUp == null).ToList();
            foreach (var plan in never) {
                Logger.Warning($"Starfront: panel {plan.PanelNumber} of {plan.Deal.ProjectName} does not rise above its floor in the next twelve hours and is left out tonight");
                plans.Remove(plan);
            }
            var ordered = plans.OrderBy(p => p.HoursUntilUp > 0 ? 1 : 0)
                               .ThenBy(p => p.HoursUntilUp > 0 ? p.HoursUntilUp : p.HoursAbove)
                               .ThenBy(p => p.PanelNumber).ToList();
            plans.Clear();
            plans.AddRange(ordered);
        }

        private StarfrontPanel BuildPanel(PanelPlan plan, Settings.StarfrontSettings settings, FrameReporter reporter) {
            var panel = new StarfrontPanel(profileService, nighttimeCalculator, plan);
            var scale = RigProfile.Scale(Runtime.Host, settings);
            var focal = profileService.ActiveProfile.TelescopeSettings.FocalLength;
            reporter.Register(new PanelContext {
                TargetName = plan.TargetName, ProjectId = plan.Deal.Project, ProjectName = plan.Deal.ProjectName,
                TaskId = plan.Deal.Id, PanelNumber = plan.PanelNumber, Filter = plan.Filters.First().Filter,
                Footprint = new Region { Ra = plan.Cell.Ra, Dec = plan.Cell.Dec, Width = plan.Cell.Width, Height = plan.Cell.Height, Rotation = plan.Cell.Rotation },
                Scale = scale, FocalLength = focal > 0 ? focal : null, Colour = settings.Colour,
                Bandpass = Filters.Bandpass(plan.Filters.First().WheelName ?? ""),
            });

            panel.Add(new AltitudeGate(panel, profileService, Runtime.Session.Run));

            Center center;
            var useRotator = settings.MatchRotation && RigProfile.HasRotator(Runtime.Host);
            if (useRotator) {
                var rotate = new CenterAndRotate(profileService, telescopeMediator, imagingMediator, rotatorMediator, filterWheelMediator,
                                                 guiderMediator, domeMediator, domeFollower, plateSolverFactory, windowServiceFactory) {
                    Name = Icons.Label("Lbl_SequenceItem_Platesolving_CenterAndRotate_Name", "Slew, center and rotate"),
                    Icon = Icons.Find("PlatesolveAndRotateSVG"),
                };
                center = rotate;
            } else {
                center = new Center(profileService, telescopeMediator, imagingMediator, filterWheelMediator, guiderMediator,
                                    domeMediator, domeFollower, plateSolverFactory, windowServiceFactory) {
                    Name = Icons.Label("Lbl_SequenceItem_Platesolving_Center_Name", "Slew and center"),
                    Icon = Icons.Find("PlatesolveSVG"),
                };
            }
            center.Category = "Starfront";
            center.Attempts = 2;
            panel.Add(center);
            panel.Add(new NoteCameraAngle(center, panel, Runtime.Host, settings, reporter));

            foreach (var block in plan.Filters) {
                if (block.WheelName != null) {
                    var wheelFilter = profileService.ActiveProfile.FilterWheelSettings.FilterWheelFilters
                        .FirstOrDefault(f => string.Equals(f.Name, block.WheelName, StringComparison.OrdinalIgnoreCase));
                    if (wheelFilter != null) {
                        panel.Add(new SwitchFilter(profileService, filterWheelMediator) {
                            Filter = wheelFilter,
                            Name = Icons.Label("Lbl_SequenceItem_FilterWheel_SwitchFilter_Name", "Switch filter"),
                            Icon = Icons.Find("FW_NoFill_SVG"),
                            Category = "Starfront",
                        });
                    }
                }
                var many = new TakeManyExposures(profileService, cameraMediator, imagingMediator, imageSaveMediator, imageHistoryVM) {
                    Name = Icons.Label("Lbl_SequenceItem_Imaging_TakeManyExposures_Name", "Take many exposures"),
                    Icon = Icons.Find("CameraSVG"),
                    Category = "Starfront",
                };
                var take = many.GetTakeExposure();
                take.ExposureTime = block.Exposure;
                take.Gain = settings.Gain;
                take.Offset = settings.Offset;
                take.Binning = new BinningMode(settings.Binning, settings.Binning);
                take.ImageType = "LIGHT";
                many.GetLoopCondition().Iterations = block.Frames;
                panel.Add(many);
            }

            if (settings.DitherEvery > 0) {
                panel.Add(new DitherAfterExposures(guiderMediator, imageHistoryVM, profileService) {
                    AfterExposures = settings.DitherEvery,
                    Name = Icons.Label("Lbl_SequenceTrigger_Guider_DitherAfterExposures_Name", "Dither after exposures"),
                    Icon = Icons.Find("DitherSVG"),
                    Category = "Starfront",
                });
            }
            return panel;
        }

        // -- the usual container duties ----------------------------------------------

        public override object Clone() {
            var copy = new StarfrontCollaborationContainer(profileService, cameraMediator, telescopeMediator, imagingMediator, imageSaveMediator,
                                                           imageHistoryVM, filterWheelMediator, guiderMediator, rotatorMediator, domeMediator,
                                                           domeFollower, plateSolverFactory, windowServiceFactory, nighttimeCalculator) {
                ProjectId = ProjectId,
            };
            copy.CopyMetaData(this);
            foreach (var condition in Conditions) { var child = (ISequenceCondition)condition.Clone(); copy.Conditions.Add(child); child.AttachNewParent(copy); }
            foreach (var trigger in Triggers) { var child = (ISequenceTrigger)trigger.Clone(); copy.Triggers.Add(child); child.AttachNewParent(copy); }
            return copy;
        }

        public override bool Validate() {
            var list = new List<string>();
            if (!Runtime.Ready) list.Add("Starfront is not set up yet");
            else if (!Runtime.Settings.Enrolled) list.Add("Sign in with Discord under Options > Plugins > Starfront Collaboration");
            else if (Runtime.Settings.Exposures.Count == 0) list.Add("Set the exposure for each filter under Options > Plugins > Starfront Collaboration");
            if (!cameraMediator.GetInfo().Connected) list.Add(Loc.Instance["LblCameraNotConnected"]);
            if (!telescopeMediator.GetInfo().Connected) list.Add(Loc.Instance["LblTelescopeNotConnected"]);
            var ok = base.Validate();
            Issues = list;
            return ok && list.Count == 0;
        }

        public override void AfterParentChanged() {
            base.AfterParentChanged();
            Validate();
        }

        public override void Teardown() {
            base.Teardown();
            if (Runtime.Ready) Runtime.Session.Updated -= OnSessionUpdated;
        }

        public override TimeSpan GetEstimatedDuration() {
            var seconds = Items.OfType<StarfrontPanel>().Sum(p => p.Plan.Seconds);
            return TimeSpan.FromSeconds(seconds);
        }

        public override string ToString() => $"Category: {Category}, Container: StarfrontCollaborationContainer, Project: {(ProjectId.Length > 0 ? ProjectId : "all")}, Panels: {Items.Count}";
    }
}
