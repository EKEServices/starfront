using NINA.Core.Utility;
using RapidStackerCollab.Core.Api;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>
    /// Live stacking: everything that sends a saved light to Rapid Stacker
    /// Collab, behind the one setting that turns it on. With the setting off
    /// nothing here is started, opened or connected, and the plugin is the
    /// plugin it was before; with it on, the sign-in, the connections and (in
    /// the phases that follow) the queue and the uploader run under it, and
    /// turning it off again stops them without a restart of N.I.N.A.
    /// </summary>
    public sealed class StackingUploader : IDisposable {
        private readonly object gate = new();
        private readonly HostServices host;
        private readonly StarfrontSettings settings;
        private readonly FrameReporter reporter;
        private readonly StarfrontSession session;
        private StackingClients clients;
        private bool disposed;

        public StackingUploader(HostServices host, StarfrontSettings settings, FrameReporter reporter, StarfrontSession session) {
            this.host = host;
            this.settings = settings;
            this.reporter = reporter;
            this.session = session;
            SignIn = new StackingSignIn(settings, () => Api);
            settings.PropertyChanged += OnSettingChanged;
        }

        public StackingSignIn SignIn { get; }

        /// <summary>Whether the setting is on and the parts are running.</summary>
        public bool Running { get; private set; }

        /// <summary>Raised on any thread when something the dock or the options page shows has changed.</summary>
        public event EventHandler Changed;

        /// <summary>
        /// The API client, made on first use and remade when the server address
        /// changes. Only called while the setting is on: the sign-in button and
        /// the uploader are both behind it.
        /// </summary>
        internal IApiClient Api {
            get {
                lock (gate) {
                    if (disposed) throw new ObjectDisposedException(nameof(StackingUploader));
                    clients ??= new StackingClients(settings);
                    return clients.Api;
                }
            }
        }

        /// <summary>Bring the parts in line with the setting: start when it is on, stop when it is off. Idempotent.</summary>
        public void Apply() {
            if (settings.StackingEnabled) Start(); else Stop();
        }

        private void Start() {
            lock (gate) {
                if (disposed || Running) return;
                Running = true;
            }
            Logger.Info("Starfront stacking: on" + (settings.StackingSignedIn ? "" : "; not signed in to Rapid Stacker Collab yet"));
            RaiseChanged();
        }

        /// <summary>Stop everything and drop the connections. Safe to call when nothing runs.</summary>
        public void Stop() {
            StackingClients old;
            lock (gate) {
                old = clients;
                clients = null;
                if (!Running) { old?.Dispose(); return; }
                Running = false;
            }
            SignIn.Cancel();
            old?.Dispose();
            Logger.Info("Starfront stacking: off");
            RaiseChanged();
        }

        private void OnSettingChanged(object sender, System.ComponentModel.PropertyChangedEventArgs e) {
            try {
                // A blank name is "everything changed": the file was loaded or
                // the profile switched. Apply is idempotent, so that is cheap.
                switch (string.IsNullOrEmpty(e.PropertyName) ? null : e.PropertyName) {
                    case nameof(StarfrontSettings.StackingEnabled):
                        Apply();
                        break;
                    case nameof(StarfrontSettings.StackingApiBase):
                        // A new server means new connections; the next use makes them.
                        StackingClients old;
                        lock (gate) { old = clients; clients = null; }
                        old?.Dispose();
                        break;
                    case null:
                        Apply();
                        break;
                }
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: a setting change could not be applied", ex);
            }
        }

        private void RaiseChanged() {
            try { Changed?.Invoke(this, EventArgs.Empty); } catch (Exception ex) { Logger.Error("Starfront stacking: a change handler failed", ex); }
        }

        public void Dispose() {
            settings.PropertyChanged -= OnSettingChanged;
            Stop();
            lock (gate) disposed = true;
        }
    }
}
