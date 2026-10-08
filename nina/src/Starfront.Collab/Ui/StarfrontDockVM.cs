using System.Collections.ObjectModel;
using System.ComponentModel.Composition;
using System.Windows;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using NINA.Astrometry;
using NINA.Astrometry.Interfaces;
using NINA.Core.Enum;
using NINA.Core.Utility;
using NINA.Core.Utility.Notification;
using NINA.Equipment.Interfaces.Mediator;
using NINA.Equipment.Interfaces.ViewModel;
using NINA.Profile.Interfaces;
using NINA.WPF.Base.Interfaces.Mediator;
using NINA.WPF.Base.Interfaces.ViewModel;
using NINA.WPF.Base.ViewModel;
using Starfront.Collab.Server;
using Starfront.Collab.Services;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Ui {

    /// <summary>One open collaboration, as the panel lists it.</summary>
    public sealed class ProjectRowVM : BaseINPC {
        public const int ThumbWidth = 320;
        public const int ThumbHeight = 200;
        private ImageSource framing;
        private string framingNote = "fetching the sky…";

        public ProjectRowVM(ProjectListing listing, DealTask task, StarfrontDockVM owner) {
            Listing = listing;
            Task = task;
            JoinCommand = new AsyncRelayCommand(() => owner.JoinAsync(listing.Id), () => CanJoin);
            LeaveCommand = new AsyncRelayCommand(() => owner.LeaveAsync(task?.Id), () => task != null);
            ShowInFramingCommand = new AsyncRelayCommand(() => owner.ShowInFramingAsync(listing), () => listing.Region != null);
        }

        public ProjectListing Listing { get; }
        public DealTask Task { get; }
        public string Id => Listing.Id;
        public string Name => Listing.Name;
        public string Coordinator => string.IsNullOrEmpty(Listing.Coordinator) ? "" : $"by {Listing.Coordinator}";
        public bool Joined => Listing.Joined || Task != null;
        public bool CanJoin => !Joined && Listing.Compatibility?.Ok == true;
        public bool Compatible => Listing.Compatibility?.Ok == true;
        public AsyncRelayCommand JoinCommand { get; }
        public AsyncRelayCommand LeaveCommand { get; }
        public AsyncRelayCommand ShowInFramingCommand { get; }

        /// <summary>The sky with the region outlined and, once joined, this telescope's panels on it.</summary>
        public ImageSource Framing {
            get => framing;
            set { framing = value; RaisePropertyChanged(); RaisePropertyChanged(nameof(HasFraming)); }
        }

        public bool HasFraming => framing != null;

        private ImageSource transit;
        private string transitText = "";

        /// <summary>The target's altitude through tonight, as a chart: how well placed it is at a glance.</summary>
        public ImageSource Transit {
            get => transit;
            set { transit = value; RaisePropertyChanged(); RaisePropertyChanged(nameof(HasTransit)); }
        }

        public bool HasTransit => transit != null;

        public string TransitText {
            get => transitText;
            set { transitText = value ?? ""; RaisePropertyChanged(); }
        }

        public string FramingNote {
            get => framingNote;
            set { framingNote = value ?? ""; RaisePropertyChanged(); }
        }

        public string FramingCaption {
            get {
                if (Listing.Region == null) return "";
                var cells = Task?.Cells?.Count ?? 0;
                if (cells == 0) return "yellow: the collaboration's region";
                var mine = Task?.Share?.Count ?? 0;
                return mine > 0 ? $"yellow: the region · white: your {cells} panels · green: tonight's {mine}" : $"yellow: the region · white: your {cells} panels";
            }
        }

        public string Detail {
            get {
                var region = Listing.Region;
                var size = region == null ? "" : $"{region.Width:0.0}° × {region.Height:0.0}°";
                var kind = Listing.Kind == "single" ? "single field" : "mosaic";
                var settings = Runtime.Ready ? Runtime.Settings : null;
                var goals = string.Join(" · ", (Listing.Goals ?? new Dictionary<string, double>())
                    .Select(kv => $"{(settings != null ? settings.FilterName(kv.Key) : kv.Key)} {kv.Value:0.#} h"));
                return string.Join(" · ", new[] { kind, size, goals }.Where(s => s.Length > 0));
            }
        }

        public string ProgressText {
            get {
                var progress = Listing.Progress ?? new Dictionary<string, ProgressFigures>();
                var collected = (Listing.Collected ?? new Dictionary<string, double>()).Values.Sum();
                if (progress.Count == 0) return collected > 0 ? $"{collected:0.0} h collected" : "nothing collected yet";
                var done = progress.Values.Average(p => p.AtGoal);
                return $"{done * 100:0}% done · {collected:0.0} h collected";
            }
        }

        public string PeopleText =>
            $"{Listing.Participants} telescope{(Listing.Participants == 1 ? "" : "s")}, {Listing.ParticipantsOnline} online"
            + (Listing.ParticipantNames?.Count > 0 ? $": {string.Join(", ", Listing.ParticipantNames)}" : "");

        public string CompatibilityText => Listing.Compatibility?.Summary ?? "";

        public string TonightText => Task == null ? "" : $"Tonight: {StarfrontSession.Describe(Task)}";
    }

    /// <summary>
    /// The Starfront panel on the Imaging tab: who is about, which
    /// collaborations are open to this telescope, and what it was dealt
    /// tonight. The host composes this in its own container, so the shared
    /// state comes through <see cref="Runtime"/> like everywhere else.
    /// </summary>
    [Export(typeof(IDockableVM))]
    public class StarfrontDockVM : DockableVM, IDisposable {
        private readonly IFramingAssistantVM framingAssistant;
        private readonly IApplicationMediator applicationMediator;
        private readonly SkyThumbnails thumbnails;
        private bool initialized;
        private string message = "";

        [ImportingConstructor]
        public StarfrontDockVM(IProfileService profileService,
                               ICameraMediator cameraMediator,
                               ITelescopeMediator telescopeMediator,
                               IFilterWheelMediator filterWheelMediator,
                               IRotatorMediator rotatorMediator,
                               IGuiderMediator guiderMediator,
                               IImageSaveMediator imageSaveMediator,
                               INighttimeCalculator nighttimeCalculator,
                               IFramingAssistantVM framingAssistant,
                               IApplicationMediator applicationMediator) : base(profileService) {
            Runtime.Ensure(new HostServices(profileService, cameraMediator, telescopeMediator, filterWheelMediator,
                                            rotatorMediator, guiderMediator, imageSaveMediator, nighttimeCalculator));
            this.framingAssistant = framingAssistant;
            this.applicationMediator = applicationMediator;
            thumbnails = new SkyThumbnails(Runtime.Settings.DataFolder);
            Title = "Starfront";
            try {
                var dictionary = new ResourceDictionary { Source = new Uri("Starfront.Collab;component/Sequencer/SequencerTemplates.xaml", UriKind.RelativeOrAbsolute) };
                if (dictionary["Starfront_CollabSVG"] is GeometryGroup geometry) {
                    geometry.Freeze();
                    ImageGeometry = geometry;
                }
            } catch (Exception ex) {
                Logger.Debug($"Starfront: panel icon - {ex.Message}");
            }
            RefreshCommand = new AsyncRelayCommand(RefreshAsync);
            SignInCommand = new AsyncRelayCommand(SignInAsync);
            Projects = new ObservableCollection<ProjectRowVM>();
            Tonight = new ObservableCollection<string>();
            PlanLines = new ObservableCollection<string>();
            Telescopes = new ObservableCollection<string>();
            Verdicts = new ObservableCollection<string>();
            Initialize();
        }

        private void Initialize() {
            if (initialized) return;
            initialized = true;
            Runtime.Session.Updated += OnUpdated;
            Runtime.Session.Run.PropertyChanged += OnRunChanged;
            Runtime.Settings.PropertyChanged += OnSettingsChanged;
            Plugin.ShuttingDown += Dispose;
            Rebuild();
        }

        private void OnRunChanged(object sender, System.ComponentModel.PropertyChangedEventArgs e) => Dispatch.OnUi(RebuildPlan);

        /// <summary>Tonight's fields with where each has got to, live while the container runs and kept afterwards.</summary>
        private void RebuildPlan() {
            if (!initialized) return;
            PlanLines.Clear();
            foreach (var item in Runtime.Session.Run.Plan) {
                var mark = item.State switch { "done" => "✓", "shooting" => "▶", "skipped" => "↷", "failed" => "✗", _ => "·" };
                var tail = string.IsNullOrEmpty(item.Detail) ? item.State : $"{item.State}, {item.Detail}";
                PlanLines.Add($"{mark} {item.Name} — {item.Frames} — {tail}");
            }
            RaisePropertyChanged(nameof(RunText));
            RaisePropertyChanged(nameof(ShowPlan));
        }

        public ObservableCollection<string> PlanLines { get; }
        public bool ShowPlan => PlanLines.Count > 0;

        public override string ContentId => typeof(StarfrontDockVM).FullName;

        public AsyncRelayCommand RefreshCommand { get; }
        public AsyncRelayCommand SignInCommand { get; }
        public ObservableCollection<ProjectRowVM> Projects { get; }
        public ObservableCollection<string> Tonight { get; }
        public ObservableCollection<string> Telescopes { get; }
        public ObservableCollection<string> Verdicts { get; }

        public bool Enrolled => Runtime.Ready && Runtime.Settings.Enrolled;
        public string StatusText => Runtime.Ready ? Runtime.Session.StatusText : "Starfront is not set up";
        public string OnlineText {
            get {
                if (!Runtime.Ready) return "";
                var presence = Runtime.Session.Presence;
                if (presence == null || presence.Telescopes.Count == 0) return "";
                return $"{presence.Online} telescope{(presence.Online == 1 ? "" : "s")} online, {presence.People} {(presence.People == 1 ? "person" : "people")}";
            }
        }
        public string RunText {
            get {
                if (!Runtime.Ready) return "";
                var run = Runtime.Session.Run;
                if (run.Running) return string.IsNullOrEmpty(run.TargetName) ? "The collaboration container is running" : $"Shooting now: {run.TargetName}";
                return run.Plan.Count > 0 ? "Last run:" : "";
            }
        }

        public string Message {
            get => message;
            private set { message = value ?? ""; RaisePropertyChanged(); }
        }

        private void OnUpdated(object sender, EventArgs e) => Dispatch.OnUi(Rebuild);
        private void OnSettingsChanged(object sender, System.ComponentModel.PropertyChangedEventArgs e) => Dispatch.OnUi(() => RaisePropertyChanged(nameof(Enrolled)));

        private void Rebuild() {
            if (!initialized) return;
            try {
                var session = Runtime.Session;
                var tasks = session.Tasks;
                Projects.Clear();
                foreach (var listing in session.Projects.OrderByDescending(p => p.Joined).ThenBy(p => p.Name)) {
                    var task = tasks.FirstOrDefault(t => t.Live && t.Project == listing.Id);
                    var row = new ProjectRowVM(listing, task, this);
                    Projects.Add(row);
                    LoadTransit(row);
                    _ = LoadFramingAsync(row);
                }
                Tonight.Clear();
                foreach (var deal in session.DealTonight()) Tonight.Add($"{deal.ProjectName}: {StarfrontSession.Describe(deal)}");
                if (Tonight.Count == 0) Tonight.Add(tasks.Count == 0 ? "No collaboration joined yet." : "Nothing dealt for tonight yet; the deal is read when the sequence runs.");
                Telescopes.Clear();
                foreach (var scope in (session.Presence?.Telescopes ?? new List<TelescopePresence>()).Where(t => t.Online)) {
                    var who = string.IsNullOrEmpty(scope.OwnerName) ? scope.Name : $"{scope.Name} · {scope.OwnerName}";
                    var doing = string.IsNullOrEmpty(scope.Target) ? scope.State : $"{scope.State} {scope.Target}";
                    Telescopes.Add($"{who} — {doing}".Trim(' ', '—'));
                }
                Verdicts.Clear();
                foreach (var verdict in session.LastVerdicts.Take(6)) Verdicts.Add(verdict);
            } catch (Exception ex) {
                Logger.Error("Starfront: the panel could not be refreshed", ex);
            }
            RaisePropertyChanged(nameof(Enrolled));
            RaisePropertyChanged(nameof(StatusText));
            RaisePropertyChanged(nameof(OnlineText));
            RebuildPlan();
        }

        private async Task RefreshAsync() {
            try {
                Message = "";
                await Runtime.Session.PollAsync(CancellationToken.None);
            } catch (Exception ex) {
                Message = ex.Message;
            }
        }

        /// <summary>The altitude chart for a row, drawn here and now: a few dozen transforms, on the UI thread it is called on.</summary>
        private void LoadTransit(ProjectRowVM row) {
            var region = row.Listing.Region;
            if (region == null || !Runtime.Ready) return;
            try {
                var floor = row.Listing.Requirements?.MinAltitude ?? 30.0;
                var (image, caption) = SkyThumbnails.Transit(region, Runtime.Host.Profile, Runtime.Host.Nighttime, floor,
                                                             ProjectRowVM.ThumbWidth, 90);
                row.Transit = image;
                row.TransitText = caption;
            } catch (Exception ex) {
                Logger.Debug($"Starfront: transit chart - {ex.Message}");
            }
        }

        /// <summary>The sky picture for a row: the cutout from the cache or CDS, then the region and panels drawn on it here.</summary>
        private async Task LoadFramingAsync(ProjectRowVM row) {
            var region = row.Listing.Region;
            if (region == null || region.Width <= 0 || region.Height <= 0) {
                row.FramingNote = "";
                return;
            }
            try {
                var cutout = await thumbnails.CutoutAsync(region, ProjectRowVM.ThumbWidth, ProjectRowVM.ThumbHeight);
                Dispatch.OnUi(() => {
                    try {
                        row.Framing = SkyThumbnails.Compose(cutout, region, row.Task?.Cells, row.Task?.Share, ProjectRowVM.ThumbWidth, ProjectRowVM.ThumbHeight);
                        row.FramingNote = cutout == null ? "no sky picture (offline?); the outline is drawn to scale" : "";
                    } catch (Exception ex) {
                        row.FramingNote = "";
                        Logger.Debug($"Starfront: framing picture - {ex.Message}");
                    }
                });
            } catch (Exception ex) {
                row.FramingNote = "";
                Logger.Debug($"Starfront: framing picture - {ex.Message}");
            }
        }

        /// <summary>Open N.I.N.A.'s Framing Assistant on the collaboration's region, at its angle, with this camera's footprint.</summary>
        public async Task ShowInFramingAsync(ProjectListing listing) {
            var region = listing?.Region;
            if (region == null) return;
            try {
                var profile = Runtime.Host.Profile.ActiveProfile;
                var coordinates = new Coordinates(Angle.ByDegree(region.Ra), Angle.ByDegree(region.Dec), Epoch.J2000);
                var dso = new DeepSkyObject(listing.Name, coordinates, profile.AstrometrySettings.Horizon) {
                    RotationPositionAngle = region.Rotation,
                };
                // Wide enough to see the whole region, within what the survey will serve.
                framingAssistant.FieldOfView = Math.Max(0.2, Math.Min(50.0, Math.Max(region.Width, region.Height) * 1.4));
                applicationMediator.ChangeTab(ApplicationTab.FRAMINGASSISTANT);
                await framingAssistant.SetCoordinates(dso);
                Message = $"Framing Assistant shows {listing.Name}: the region is {region.Width:0.0}° × {region.Height:0.0}°; the rectangle is your camera.";
            } catch (Exception ex) {
                Message = $"Could not open the Framing Assistant: {ex.Message}";
                Logger.Error("Starfront: framing assistant", ex);
            }
        }

        private async Task SignInAsync() {
            try {
                Message = "";
                await Runtime.Session.SignInAsync(CancellationToken.None);
            } catch (Exception ex) {
                Message = $"Sign-in failed: {ex.Message}";
            }
        }

        public async Task JoinAsync(string projectId) {
            try {
                Message = "Joining...";
                var said = await Runtime.Session.JoinAsync(projectId, CancellationToken.None);
                Message = said;
                Notification.ShowSuccess($"Starfront: {said}");
            } catch (Exception ex) {
                Message = $"Could not join: {ex.Message}";
                Notification.ShowWarning($"Starfront: {ex.Message}");
            }
        }

        public async Task LeaveAsync(string taskId) {
            if (string.IsNullOrEmpty(taskId)) return;
            try {
                Message = "Leaving...";
                await Runtime.Session.LeaveAsync(taskId, CancellationToken.None);
                Message = "Left the collaboration. Frames already shot stay credited.";
            } catch (Exception ex) {
                Message = $"Could not leave: {ex.Message}";
            }
        }

        public void Dispose() {
            if (!initialized) return;
            initialized = false;
            if (Runtime.Ready) {
                Runtime.Session.Updated -= OnUpdated;
                Runtime.Session.Run.PropertyChanged -= OnRunChanged;
                Runtime.Settings.PropertyChanged -= OnSettingsChanged;
            }
            Plugin.ShuttingDown -= Dispose;
        }
    }
}
