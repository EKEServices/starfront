using System.IO;
using System.Threading.Channels;
using NINA.Core.Utility;
using NINA.WPF.Base.Interfaces.Mediator;
using RapidStackerCollab.Core.Api;
using RapidStackerCollab.Core.Frames;
using RapidStackerCollab.Core.Upload;
using RapidStackerCollab.Core.Util;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>
    /// Live stacking: everything that sends a saved light to Rapid Stacker
    /// Collab, behind the one setting that turns it on. With the setting off
    /// nothing here is started, opened, hooked or connected, and the plugin is
    /// the plugin it was before. With it on: the links learn their join codes
    /// from each check-in and join; every light the Starfront container
    /// saves is looked up by its target name, exactly as the ledger does, and
    /// queued for its collaboration's stacking project; a runner sends the
    /// queue when the policy allows, through a copy with the observer's
    /// location removed; and the verdicts come back into the queue for the
    /// dock to show. Turning the setting off stops all of it without a
    /// restart. Nothing in here can stop a night: every entry N.I.N.A. calls
    /// catches everything and logs.
    /// </summary>
    public sealed class StackingUploader : IDisposable {
        private readonly object gate = new();
        private readonly object clientsGate = new();
        private readonly object notedGate = new();
        private readonly HostServices host;
        private readonly StarfrontSettings settings;
        private readonly FrameReporter reporter;
        private readonly StarfrontSession session;
        private readonly HashSet<string> unlinkedNoted = new(StringComparer.Ordinal);
        private StackingClients clients;
        private bool disposed;

        // The running parts; null while the setting is off.
        private CancellationTokenSource cts;
        private UploadQueue queue;
        private BandwidthLimiter limiter;
        private UploadRunner runner;
        private StackingIntake intake;
        private VerdictPoller verdicts;
        private CameraStateConsumer cameraConsumer;
        private Channel<(SavedFrame Frame, StackingLink Link)> frames;
        private readonly List<Task> tasks = new();

        public StackingUploader(HostServices host, StarfrontSettings settings, FrameReporter reporter, StarfrontSession session) {
            this.host = host;
            this.settings = settings;
            this.reporter = reporter;
            this.session = session;
            Links = new StackingLinks(Path.Combine(settings.DataFolder, "stacking", "links.json"));
            Imaging = new ImagingStateTracker();
            SignIn = new StackingSignIn(settings, () => Api);
            Joiner = new StackingJoiner(host, settings, Links, () => Api);
            Joiner.AuthRequired += (_, _) => OnAuthRequired();
            SignIn.Changed += OnSignInChanged;
            Links.Changed += (_, _) => RaiseChanged();
            settings.PropertyChanged += OnSettingChanged;
        }

        public StackingSignIn SignIn { get; }
        public StackingLinks Links { get; }
        public StackingJoiner Joiner { get; }
        public ImagingStateTracker Imaging { get; }

        /// <summary>Whether the setting is on and the parts are running.</summary>
        public bool Running { get; private set; }

        public string DataFolder => Path.Combine(settings.DataFolder, "stacking");
        public string TempDirectory => Path.Combine(DataFolder, "tmp");

        /// <summary>The file being sent and how far it is, for the dock; null between files.</summary>
        public (QueueItem Item, UploadProgress Progress)? Current { get; private set; }

        /// <summary>The last file that finished and how, for the dock.</summary>
        public string LastResult { get; private set; } = "";

        /// <summary>Why the runner is waiting, when it is.</summary>
        public PolicyDecision Decision => runner?.LastDecision;

        public bool AuthRequired => runner?.AuthRequired ?? SignIn.AuthRequired;

        /// <summary>Raised on any thread when something the dock or the options page shows has changed.</summary>
        public event EventHandler Changed;

        /// <summary>How many rows the queue holds in each state; empty while off.</summary>
        public IReadOnlyDictionary<string, int> Counts() {
            var q = queue;
            if (q == null) return new Dictionary<string, int>();
            try { return q.CountsByState(); } catch (Exception) { return new Dictionary<string, int>(); }
        }

        /// <summary>The API client, made on first use. Only called while the setting is on.</summary>
        internal IApiClient Api {
            get {
                lock (clientsGate) {
                    if (disposed) throw new ObjectDisposedException(nameof(StackingUploader));
                    clients ??= new StackingClients(settings);
                    return clients.Api;
                }
            }
        }

        private StackingClients EnsureClients() {
            lock (clientsGate) return clients ??= new StackingClients(settings);
        }

        private void DropClients() {
            StackingClients old;
            lock (clientsGate) { old = clients; clients = null; }
            old?.Dispose();
        }

        // -- lifetime ------------------------------------------------------------

        /// <summary>Bring the parts in line with the setting. Idempotent; safe from any thread.</summary>
        public void Apply() {
            if (settings.StackingEnabled) Start(); else Stop();
        }

        private void Start() {
            lock (gate) {
                if (disposed || Running) return;
                try {
                    NativeSqlite.Preload();
                    Directory.CreateDirectory(TempDirectory);
                    Links.Load();
                    cts = new CancellationTokenSource();
                    queue = new UploadQueue(Path.Combine(DataFolder, "queue.db"), SystemClock.Instance);
                    var recovered = queue.RecoverInterrupted();
                    if (recovered > 0) Logger.Info($"Starfront stacking: {recovered} upload(s) interrupted last time will resume");
                    var connections = EnsureClients();
                    limiter = new BandwidthLimiter(SystemClock.Instance, settings.StackingBandwidthKBps);
                    var engine = new UploadEngine(connections.Api, connections.S3Http, SystemClock.Instance, limiter);
                    runner = new UploadRunner(queue, engine, new UploadPolicy(SystemClock.Instance), settings.StackingPolicy, CurrentImagingState,
                                              SystemClock.Instance, new UploadRunnerOptions {
                                                  TempDirectory = TempDirectory,
                                                  StripPredicate = item => settings.StackingStripLocation || Links.RequireStrip(item.ProjectRef),
                                              });
                    runner.OnProgress = (item, progress) => { Current = (item, progress); RaiseChanged(); };
                    runner.OnItemFinished = OnItemFinished;
                    runner.OnAuthRequired = OnAuthRequired;
                    intake = new StackingIntake(queue, settings);
                    intake.Enqueued += _ => runner.Wake();
                    verdicts = new VerdictPoller(() => Api, queue);
                    Imaging.Changed += runner.Wake;
                    frames = Channel.CreateUnbounded<(SavedFrame, StackingLink)>(new UnboundedChannelOptions { SingleReader = true });
                    cameraConsumer = new CameraStateConsumer(Imaging);
                    host.Camera.RegisterConsumer(cameraConsumer);
                    host.ImageSave.ImageSaved += OnImageSaved;
                    session.Updated += OnSessionUpdated;
                    var token = cts.Token;
                    tasks.Clear();
                    tasks.Add(Task.Run(() => DrainAsync(frames.Reader, token)));
                    tasks.Add(Task.Run(() => RunnerLoopAsync(runner, token)));
                    tasks.Add(Task.Run(() => MaintenanceLoopAsync(token)));
                    tasks.Add(Task.Run(() => VerdictLoopAsync(token)));
                    Running = true;
                } catch (Exception ex) {
                    Logger.Error("Starfront stacking could not start; it is off until N.I.N.A. is restarted or the setting is turned off and on", ex);
                    TearDownParts();
                    return;
                }
            }
            Logger.Info("Starfront stacking: on" + (settings.StackingSignedIn ? "" : "; not signed in to Rapid Stacker Collab yet"));
            _ = ReconcileAsync();
            RaiseChanged();
        }

        /// <summary>Stop everything, waiting a bounded time for the loops, and drop the connections. Safe to call when nothing runs.</summary>
        public void Stop() {
            lock (gate) {
                if (!Running) {
                    DropClients();
                    return;
                }
                Running = false;
                TearDownParts();
            }
            SignIn.Cancel();
            Logger.Info("Starfront stacking: off");
            RaiseChanged();
        }

        /// <summary>Under the lock. Unhook, cancel, wait, dispose, in that order, so nothing lands in a queue being closed.</summary>
        private void TearDownParts() {
            try { host.ImageSave.ImageSaved -= OnImageSaved; } catch (Exception) { }
            try { session.Updated -= OnSessionUpdated; } catch (Exception) { }
            if (cameraConsumer != null) { try { host.Camera.RemoveConsumer(cameraConsumer); } catch (Exception) { } }
            if (runner != null) Imaging.Changed -= runner.Wake;
            try { frames?.Writer.TryComplete(); } catch (Exception) { }
            try { cts?.Cancel(); } catch (Exception) { }
            foreach (var task in tasks) {
                try { task.Wait(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            }
            tasks.Clear();
            try { queue?.Dispose(); } catch (Exception) { }
            try { cts?.Dispose(); } catch (Exception) { }
            cts = null; queue = null; limiter = null; runner = null; intake = null; verdicts = null; cameraConsumer = null; frames = null;
            Current = null;
            DropClients();
        }

        public void Dispose() {
            settings.PropertyChanged -= OnSettingChanged;
            Stop();
            lock (clientsGate) disposed = true;
        }

        // -- what N.I.N.A. and the session call ------------------------------------

        /// <summary>
        /// Every saved image, on a thread-pool thread after the file is at its
        /// final path. A light is matched to its collaboration by the target
        /// name N.I.N.A. stamped, exactly as the ledger does it, and posted to
        /// the intake; a calibration frame, when asked for, goes to the
        /// project of the last light sent, calibration having no target of
        /// its own. Cheap, and never throws.
        /// </summary>
        private void OnImageSaved(object sender, ImageSavedEventArgs args) {
            try {
                Imaging.ImageSaved();
                var path = args.PathToImage?.LocalPath;
                if (string.IsNullOrEmpty(path)) return;
                var type = args.MetaData?.Image?.ImageType ?? "";
                StackingLink link;
                if (type == "LIGHT") {
                    var context = reporter.Lookup(args.MetaData?.Target?.Name);
                    if (context == null) return;                 // not shot for a collaboration
                    link = Links.Lookup(context.ProjectId);
                    if (link == null || !link.Feeds) {
                        if (NoteOnce(context.ProjectId)) {
                            Logger.Info($"Starfront stacking: {context.ProjectName} is not linked to a stacking project"
                                        + (link == null ? "" : $" ({link.Describe()})") + "; its lights stay here");
                        }
                        return;
                    }
                } else {
                    if (!settings.StackingIncludeCalibration) return;
                    link = Links.Lookup(reporter.LastProject);
                    if (link == null || !link.Feeds) {
                        if (NoteOnce("calibration")) Logger.Info("Starfront stacking: a calibration frame was saved before any light of a linked collaboration; it stays here");
                        return;
                    }
                }
                var frame = new SavedFrame(path, args.Filter, args.Duration, args.IsBayered, args.FileType.ToString());
                frames?.Writer.TryWrite((frame, link));
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: a saved frame could not be handed to the uploader", ex);
            }
        }

        /// <summary>Say a thing once per session, not once per frame.</summary>
        private bool NoteOnce(string key) {
            lock (notedGate) return unlinkedNoted.Add(key);
        }

        private void OnSessionUpdated(object sender, EventArgs e) {
            _ = ReconcileAsync();
            runner?.Wake();
        }

        private async Task ReconcileAsync() {
            var token = cts?.Token ?? CancellationToken.None;
            try {
                await Joiner.ReconcileAsync(session.Projects, token).ConfigureAwait(false);
            } catch (OperationCanceledException) {
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: the links could not be brought up to date", ex);
            }
            runner?.Wake();
        }

        private void OnSignInChanged(object sender, EventArgs e) {
            if (settings.StackingSignedIn) {
                runner?.ClearAuthRequired();
                _ = ReconcileAsync();
            }
            runner?.Wake();
            RaiseChanged();
        }

        private void OnAuthRequired() {
            SignIn.MarkAuthRequired();
            RaiseChanged();
        }

        private void OnItemFinished(QueueItem item, UploadResult result) {
            Current = null;
            LastResult = result.Outcome switch {
                UploadOutcome.Uploaded => $"{item.Filename} sent" + (result.Resumed ? " (resumed)" : ""),
                UploadOutcome.Duplicate => $"{item.Filename} was already there",
                UploadOutcome.RetryLater => $"{item.Filename} will be tried again: {result.Message}",
                UploadOutcome.ProjectBlocked => $"{item.Filename} is waiting: {result.Message}",
                UploadOutcome.AuthRequired => "sign in to Rapid Stacker Collab again",
                _ => $"{item.Filename} failed: {result.Message}",
            };
            switch (result.Outcome) {
                case UploadOutcome.Uploaded: Logger.Info($"Starfront stacking: sent {item.Filename}"); break;
                case UploadOutcome.Duplicate: Logger.Info($"Starfront stacking: {item.Filename} was already on the server"); break;
                case UploadOutcome.Failed: Logger.Warning($"Starfront stacking: {item.Filename} will not be sent - {result.Message}"); break;
                default: Logger.Info($"Starfront stacking: {item.Filename} {result.Outcome} - {result.Message}"); break;
            }
            RaiseChanged();
        }

        // -- the buttons -----------------------------------------------------------

        public void UploadNow() {
            Imaging.RequestUploadNow();
            runner?.Wake();
        }

        public void TogglePause() {
            settings.StackingPaused = !settings.StackingPaused;
            runner?.Wake();
        }

        // -- the loops -------------------------------------------------------------

        /// <summary>Upload now lasts only while something is due, so it cannot linger and override the mode for later frames.</summary>
        private ImagingState CurrentImagingState() {
            var state = Imaging.Snapshot();
            var q = queue;
            if (state.UploadNowRequested && q != null && !(q.NextDueAt() is DateTimeOffset due && due <= DateTimeOffset.Now)) {
                Imaging.ClearUploadNow();
                state = Imaging.Snapshot();
            }
            return state;
        }

        private async Task DrainAsync(ChannelReader<(SavedFrame Frame, StackingLink Link)> reader, CancellationToken ct) {
            try {
                while (await reader.WaitToReadAsync(ct).ConfigureAwait(false)) {
                    while (reader.TryRead(out var next)) {
                        try {
                            var current = intake;
                            if (current == null) return;
                            var decision = current.Evaluate(next.Frame.Path, next.Frame.Filter, next.Link, settings.StackingIncludeCalibration);
                            if (decision.Enqueue) {
                                var added = current.Enqueue(next.Frame.Path, decision, next.Link);
                                if (added?.Added == true) Logger.Info($"Starfront stacking: {Path.GetFileName(next.Frame.Path)} {decision.Reason}");
                            } else {
                                Logger.Info($"Starfront stacking: not sending {Path.GetFileName(next.Frame.Path)}: {decision.Reason}");
                            }
                        } catch (Exception ex) {
                            Logger.Error("Starfront stacking: a saved frame could not be queued", ex);
                        }
                    }
                }
            } catch (OperationCanceledException) {
            }
        }

        private static async Task RunnerLoopAsync(UploadRunner runner, CancellationToken ct) {
            while (!ct.IsCancellationRequested) {
                try {
                    await runner.RunAsync(ct).ConfigureAwait(false);
                    return;
                } catch (OperationCanceledException) when (ct.IsCancellationRequested) {
                    return;
                } catch (Exception ex) {
                    // The runner is not meant to throw; if it does, say so and start it again rather than stop uploading silently.
                    Logger.Error("Starfront stacking: the upload loop failed and will restart", ex);
                    try { await Task.Delay(TimeSpan.FromSeconds(30), ct).ConfigureAwait(false); } catch (OperationCanceledException) { return; }
                }
            }
        }

        private async Task MaintenanceLoopAsync(CancellationToken ct) {
            while (!ct.IsCancellationRequested) {
                try {
                    TempCleaner.Sweep(TempDirectory, TimeSpan.FromHours(settings.StackingTempRetentionHours), DateTimeOffset.Now);
                    queue?.Purge(DateTimeOffset.Now - TimeSpan.FromDays(settings.StackingHistoryDays));
                    await Joiner.RefreshMembershipsAsync(ct).ConfigureAwait(false);
                    await Task.Delay(TimeSpan.FromMinutes(30), ct).ConfigureAwait(false);
                } catch (OperationCanceledException) when (ct.IsCancellationRequested) {
                    return;
                } catch (Exception ex) {
                    Logger.Error("Starfront stacking: maintenance failed", ex);
                    try { await Task.Delay(TimeSpan.FromMinutes(5), ct).ConfigureAwait(false); } catch (OperationCanceledException) { return; }
                }
            }
        }

        private async Task VerdictLoopAsync(CancellationToken ct) {
            while (!ct.IsCancellationRequested) {
                try {
                    var waiting = queue?.AwaitingVerdict(1).Count > 0;
                    if (waiting && settings.StackingSignedIn) {
                        var learnt = await verdicts.PollAsync(ct).ConfigureAwait(false);
                        if (learnt > 0) RaiseChanged();
                    }
                    await Task.Delay(waiting ? TimeSpan.FromSeconds(30) : TimeSpan.FromMinutes(5), ct).ConfigureAwait(false);
                } catch (OperationCanceledException) when (ct.IsCancellationRequested) {
                    return;
                } catch (Exception ex) {
                    Logger.Debug($"Starfront stacking: the verdicts could not be read - {ex.Message}");
                    try { await Task.Delay(TimeSpan.FromMinutes(2), ct).ConfigureAwait(false); } catch (OperationCanceledException) { return; }
                }
            }
        }

        // -- settings --------------------------------------------------------------

        private void OnSettingChanged(object sender, System.ComponentModel.PropertyChangedEventArgs e) {
            try {
                // A blank name is "everything changed": the file was loaded or
                // the profile switched. Apply is idempotent, so that is cheap.
                switch (string.IsNullOrEmpty(e.PropertyName) ? null : e.PropertyName) {
                    case nameof(StarfrontSettings.StackingEnabled):
                    case null:
                        // Off the UI thread: stopping waits for the loops.
                        Task.Run(Apply);
                        break;
                    case nameof(StarfrontSettings.StackingApiBase):
                        // New connections for a new server: the running parts hold the old ones, so they go round.
                        Task.Run(() => { Stop(); Apply(); });
                        break;
                    case nameof(StarfrontSettings.StackingBandwidthKBps):
                        limiter?.SetLimit(settings.StackingBandwidthKBps);
                        runner?.Wake();
                        break;
                    case nameof(StarfrontSettings.StackingPaused):
                    case nameof(StarfrontSettings.StackingUploadMode):
                    case nameof(StarfrontSettings.StackingWindowStart):
                    case nameof(StarfrontSettings.StackingWindowEnd):
                    case nameof(StarfrontSettings.StackingIdleGraceSeconds):
                    case nameof(StarfrontSettings.StackingStripLocation):
                        runner?.Wake();
                        RaiseChanged();
                        break;
                }
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: a setting change could not be applied", ex);
            }
        }

        private void RaiseChanged() {
            try { Changed?.Invoke(this, EventArgs.Empty); } catch (Exception ex) { Logger.Error("Starfront stacking: a change handler failed", ex); }
        }
    }
}
