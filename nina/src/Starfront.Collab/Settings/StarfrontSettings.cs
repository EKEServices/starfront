using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NINA.Core.Utility;
using NINA.Profile;
using NINA.Profile.Interfaces;

namespace Starfront.Collab.Settings {

    /// <summary>
    /// Everything the plugin remembers.
    ///
    /// Two homes, because two kinds of thing. What belongs to the *person and
    /// this PC* - the server, the Discord sign-in, the telescope's token - is
    /// global, in the plugin's own file under N.I.N.A.'s application data,
    /// with the credentials protected for this Windows account (see
    /// <see cref="Protected"/>). What belongs to the *equipment* - the exposure
    /// each filter is shot at, the camera's angle, gain, binning - is per
    /// N.I.N.A. profile, through the host's own plugin-settings store, so a
    /// second profile for a second camera keeps its own table.
    /// </summary>
    public sealed class StarfrontSettings : BaseINPC {
        public const string DefaultServer = "https://collab.starfront.space";

        /// <summary>Addresses the server used to live at; one typed in is pointed at the current one.</summary>
        private static readonly string[] LegacyServers = { "https://starfront-bray.duckdns.org" };

        private readonly IProfileService profiles;
        private readonly PluginOptionsAccessor options;
        private readonly object gate = new();
        private readonly string globalPath;
        private GlobalSettings global = new();

        public StarfrontSettings(IProfileService profiles) {
            this.profiles = profiles;
            var id = typeof(StarfrontSettings).Assembly.GetCustomAttributes(typeof(GuidAttribute), false)
                .OfType<GuidAttribute>().First().Value;
            PluginId = Guid.Parse(id);
            options = new PluginOptionsAccessor(profiles, PluginId);
            DataFolder = Path.Combine(CoreUtil.APPLICATIONTEMPPATH, "PluginSettings", PluginId.ToString("D").ToUpperInvariant());
            globalPath = Path.Combine(DataFolder, "settings.json");
            profiles.ProfileChanged += (_, _) => RaiseAllPropertiesChanged();
        }

        public Guid PluginId { get; }
        public string DataFolder { get; }

        // -- the file --------------------------------------------------------

        private sealed class GlobalSettings {
            public int Version { get; set; } = 1;
            public string ServerUrl { get; set; } = "";
            public string UserToken { get; set; } = "";
            public string UserName { get; set; } = "";
            public string UserId { get; set; } = "";
            public string AgentToken { get; set; } = "";
            public string AgentId { get; set; } = "";
            public string AgentName { get; set; } = "";
            public bool SharePosition { get; set; } = true;
            public int PollMinutes { get; set; } = 10;
        }

        public void Load() {
            lock (gate) {
                try {
                    if (File.Exists(globalPath)) {
                        var text = File.ReadAllText(globalPath);
                        global = JsonConvert.DeserializeObject<GlobalSettings>(text) ?? new GlobalSettings();
                    }
                } catch (Exception ex) {
                    // A corrupt file is kept aside rather than overwritten, and
                    // the plugin starts signed out.
                    Logger.Error("Starfront settings could not be read; starting afresh", ex);
                    try { File.Copy(globalPath, globalPath + ".broken", true); } catch (Exception) { }
                    global = new GlobalSettings();
                }
            }
            RaiseAllPropertiesChanged();
        }

        private void Save() {
            lock (gate) {
                Directory.CreateDirectory(DataFolder);
                var temporary = globalPath + ".tmp";
                File.WriteAllText(temporary, JsonConvert.SerializeObject(global, Formatting.Indented));
                File.Move(temporary, globalPath, true);
            }
        }

        // -- the person and this PC -------------------------------------------

        /// <summary>The collaboration server, without a trailing slash. Blank means the built-in one.</summary>
        public string ServerUrl {
            get {
                var url = (global.ServerUrl ?? "").Trim().TrimEnd('/');
                if (LegacyServers.Contains(url)) url = "";
                return url.Length > 0 ? url : DefaultServer;
            }
            set {
                var url = (value ?? "").Trim().TrimEnd('/');
                if (url == DefaultServer || LegacyServers.Contains(url)) url = "";
                global.ServerUrl = url;
                Save();
                RaisePropertyChanged();
            }
        }

        public string UserToken {
            get => Protected.Unwrap(global.UserToken);
            set { global.UserToken = Protected.Wrap(value); Save(); RaisePropertyChanged(); RaisePropertyChanged(nameof(SignedIn)); }
        }

        public string UserName {
            get => global.UserName ?? "";
            set { global.UserName = value ?? ""; Save(); RaisePropertyChanged(); }
        }

        public string UserId {
            get => global.UserId ?? "";
            set { global.UserId = value ?? ""; Save(); RaisePropertyChanged(); }
        }

        /// <summary>Whether somebody has signed in on this PC. The telescope's own token is what the night runs on.</summary>
        public bool SignedIn => UserToken.Length > 0 || Enrolled;

        public string AgentToken {
            get => Protected.Unwrap(global.AgentToken);
            set { global.AgentToken = Protected.Wrap(value); Save(); RaisePropertyChanged(); RaisePropertyChanged(nameof(Enrolled)); RaisePropertyChanged(nameof(SignedIn)); }
        }

        public string AgentId {
            get => global.AgentId ?? "";
            set { global.AgentId = value ?? ""; Save(); RaisePropertyChanged(); }
        }

        public string AgentName {
            get => global.AgentName ?? "";
            set { global.AgentName = value ?? ""; Save(); RaisePropertyChanged(); }
        }

        /// <summary>Whether this PC holds a telescope token - the credential the night runs on.</summary>
        public bool Enrolled => AgentToken.Length > 0;

        public bool SharePosition {
            get => global.SharePosition;
            set { global.SharePosition = value; Save(); RaisePropertyChanged(); }
        }

        public int PollMinutes {
            get => Math.Max(1, global.PollMinutes);
            set { global.PollMinutes = Math.Max(1, value); Save(); RaisePropertyChanged(); }
        }

        /// <summary>Forget the sign-in and the telescope token on this PC.</summary>
        public void ForgetCredentials() {
            global.UserToken = "";
            global.UserName = "";
            global.UserId = "";
            global.AgentToken = "";
            global.AgentId = "";
            global.AgentName = "";
            Save();
            RaiseAllPropertiesChanged();
        }

        // -- the equipment: per N.I.N.A. profile -------------------------------

        /// <summary>What this telescope is called on the chart: the profile's name unless typed over.</summary>
        public string TelescopeName {
            get {
                var typed = options.GetValueString(nameof(TelescopeName), "");
                return typed.Length > 0 ? typed : (profiles.ActiveProfile?.Name ?? "Telescope");
            }
            set { options.SetValueString(nameof(TelescopeName), (value ?? "").Trim()); RaisePropertyChanged(); }
        }

        /// <summary>Seconds per filter, by the filter's name as the wheel spells it.</summary>
        public Dictionary<string, double> Exposures {
            get {
                var text = options.GetValueString(nameof(Exposures), "{}");
                try {
                    var table = JsonConvert.DeserializeObject<Dictionary<string, double>>(text) ?? new Dictionary<string, double>();
                    return table.Where(kv => kv.Value > 0).ToDictionary(kv => kv.Key, kv => kv.Value);
                } catch (Exception) {
                    return new Dictionary<string, double>();
                }
            }
            set {
                var clean = (value ?? new Dictionary<string, double>()).Where(kv => kv.Value > 0 && !string.IsNullOrWhiteSpace(kv.Key))
                    .ToDictionary(kv => kv.Key.Trim(), kv => Math.Round(kv.Value, 3));
                options.SetValueString(nameof(Exposures), JsonConvert.SerializeObject(clean));
                RaisePropertyChanged();
            }
        }

        public void SetExposure(string filter, double? seconds) {
            var table = Exposures;
            if (seconds is > 0) table[filter.Trim()] = seconds.Value; else table.Remove(filter.Trim());
            Exposures = table;
        }

        /// <summary>The exposure for a filter, matched by the one spelling of its name; null when unset.</summary>
        public double? ExposureFor(string filter) {
            var wanted = Sky.Filters.Canonical(filter).ToLowerInvariant();
            foreach (var kv in Exposures) {
                if (Sky.Filters.Canonical(kv.Key).ToLowerInvariant() == wanted) return kv.Value;
            }
            return null;
        }

        /// <summary>Hours a night this telescope gives collaborations; 0 lets the server assume its default.</summary>
        public double HoursPerNight {
            get => options.GetValueDouble(nameof(HoursPerNight), 0.0);
            set { options.SetValueDouble(nameof(HoursPerNight), Math.Max(0.0, Math.Min(24.0, value))); RaisePropertyChanged(); }
        }

        /// <summary>Dither after this many frames of a panel; 0 leaves dithering to the triggers around the container.</summary>
        public int DitherEvery {
            get => options.GetValueInt32(nameof(DitherEvery), 0);
            set { options.SetValueInt32(nameof(DitherEvery), Math.Max(0, value)); RaisePropertyChanged(); }
        }

        /// <summary>Turn the camera to the project's angle when a rotator is connected.</summary>
        public bool MatchRotation {
            get => options.GetValueBoolean(nameof(MatchRotation), true);
            set { options.SetValueBoolean(nameof(MatchRotation), value); RaisePropertyChanged(); }
        }

        /// <summary>The camera's position angle on the sky for a rig without a rotator, from its last plate solve. NaN until measured.</summary>
        public double CameraAngle {
            get => options.GetValueDouble(nameof(CameraAngle), double.NaN);
            set { options.SetValueDouble(nameof(CameraAngle), value); RaisePropertyChanged(); RaisePropertyChanged(nameof(CameraAngleText)); }
        }

        public string CameraAngleText => double.IsNaN(CameraAngle) ? "not measured yet" : CameraAngle.ToString("0.0", CultureInfo.InvariantCulture) + "°";

        /// <summary>Sensor size in pixels, remembered from the last connected camera so the afternoon knows it too.</summary>
        public int SensorWidth {
            get => options.GetValueInt32(nameof(SensorWidth), 0);
            set { options.SetValueInt32(nameof(SensorWidth), value); RaisePropertyChanged(); }
        }

        public int SensorHeight {
            get => options.GetValueInt32(nameof(SensorHeight), 0);
            set { options.SetValueInt32(nameof(SensorHeight), value); RaisePropertyChanged(); }
        }

        /// <summary>Pixel size in microns as the camera reported it; falls back to the profile's.</summary>
        public double PixelSize {
            get => options.GetValueDouble(nameof(PixelSize), 0.0);
            set { options.SetValueDouble(nameof(PixelSize), value); RaisePropertyChanged(); }
        }

        /// <summary>A one-shot colour camera, as last seen connected.</summary>
        public bool Colour {
            get => options.GetValueBoolean(nameof(Colour), false);
            set { options.SetValueBoolean(nameof(Colour), value); RaisePropertyChanged(); }
        }

        /// <summary>Gain for collaboration frames; -1 is the camera's default.</summary>
        public int Gain {
            get => options.GetValueInt32(nameof(Gain), -1);
            set { options.SetValueInt32(nameof(Gain), Math.Max(-1, value)); RaisePropertyChanged(); }
        }

        public int Offset {
            get => options.GetValueInt32(nameof(Offset), -1);
            set { options.SetValueInt32(nameof(Offset), Math.Max(-1, value)); RaisePropertyChanged(); }
        }

        public short Binning {
            get => options.GetValueInt16(nameof(Binning), 1);
            set { options.SetValueInt16(nameof(Binning), (short)Math.Max(1, Math.Min(4, (int)value))); RaisePropertyChanged(); }
        }

        /// <summary>The filters the profile knows, as the wheel spells them.</summary>
        public List<string> ProfileFilterNames() {
            var names = profiles.ActiveProfile?.FilterWheelSettings?.FilterWheelFilters?
                .Select(f => (f.Name ?? "").Trim()).Where(n => n.Length > 0).Distinct().ToList() ?? new List<string>();
            return names;
        }
    }
}
