using Newtonsoft.Json.Linq;
using NINA.Core.Enum;
using NINA.Core.Utility;
using Starfront.Collab.Ledger;
using Starfront.Collab.Settings;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Services {

    /// <summary>
    /// What the server needs to know about this telescope to decide whether
    /// it can help and how to tile its sky: assembled from the N.I.N.A.
    /// profile, the connected camera when there is one, and what the plugin
    /// has measured for itself. The same shape Starfront sends.
    /// </summary>
    public static class RigProfile {

        public static double? Scale(HostServices host, StarfrontSettings settings) {
            var focal = host.Profile.ActiveProfile.TelescopeSettings.FocalLength;
            var pixel = settings.PixelSize > 0 ? settings.PixelSize : host.Profile.ActiveProfile.CameraSettings.PixelSize;
            if (focal <= 0 || pixel <= 0) return null;
            return 206.265 * pixel * Math.Max(1, (int)settings.Binning) / focal;
        }

        /// <summary>
        /// The sensor in pixels: what a connected camera said, else what the
        /// Framing Assistant remembers of the last camera, which N.I.N.A. keeps in
        /// the profile. Zero by zero until either is known. Without the fallback a
        /// telescope whose camera had not been connected since the plugin was
        /// installed could not join, though its focal length and pixel size were set.
        /// </summary>
        public static (int width, int height) Sensor(HostServices host, StarfrontSettings settings) {
            if (settings.SensorWidth > 0 && settings.SensorHeight > 0) return (settings.SensorWidth, settings.SensorHeight);
            var framing = host.Profile.ActiveProfile.FramingAssistantSettings;
            if (framing.CameraWidth > 0 && framing.CameraHeight > 0) return (framing.CameraWidth, framing.CameraHeight);
            return (0, 0);
        }

        /// <summary>The camera's field on the sky in degrees, width by height, or null until the sensor is known.</summary>
        public static (double width, double height)? Field(HostServices host, StarfrontSettings settings) {
            var scale = Scale(host, settings);
            var (width, height) = Sensor(host, settings);
            if (scale == null || width <= 0 || height <= 0) return null;
            var binning = Math.Max(1, (int)settings.Binning);
            return (scale.Value * (width / (double)binning) / 3600.0,
                    scale.Value * (height / (double)binning) / 3600.0);
        }

        /// <summary>
        /// What the server will be told this telescope can see, in one line for
        /// the options page - or what is missing and where to set it, so "it says
        /// my focal length is not set" can be answered by looking.
        /// </summary>
        public static string Describe(HostServices host, StarfrontSettings settings) {
            RememberCamera(host, settings);
            var profile = host.Profile.ActiveProfile;
            var focal = profile.TelescopeSettings.FocalLength;
            var pixel = settings.PixelSize > 0 ? settings.PixelSize : profile.CameraSettings.PixelSize;
            var (width, height) = Sensor(host, settings);
            var missing = new List<string>();
            if (focal <= 0) missing.Add("the focal length (Options > Equipment > Telescope)");
            if (pixel <= 0) missing.Add("the pixel size (Options > Equipment > Camera)");
            if (width <= 0 || height <= 0) missing.Add("the sensor size (connect the camera once)");
            if (missing.Count > 0) return "Not known yet: " + string.Join(", ", missing) + ".";
            var field = Field(host, settings);
            var scale = Scale(host, settings) ?? 0;
            var binned = Math.Max(1, (int)settings.Binning) > 1 ? $" binned {(int)settings.Binning}×" : "";
            return $"{width}×{height} px at {pixel:0.##} µm, {focal:0.#} mm{binned}: {scale:0.##}″/px, "
                 + $"{field?.width ?? 0:0.##}° × {field?.height ?? 0:0.##}°"
                 + (settings.Colour ? ", colour camera" : ", mono camera");
        }

        /// <summary>How far two camera angles differ, half a turn counting as the same angle (the same rectangle on the sky).</summary>
        public static double HalfTurnDifference(double a, double b) {
            var d = ((a - b + 90.0) % 180.0 + 180.0) % 180.0 - 90.0;
            return Math.Abs(d);
        }

        /// <summary>Remember what a connected camera says about itself, so the afternoon knows it too.</summary>
        public static void RememberCamera(HostServices host, StarfrontSettings settings) {
            var camera = host.Camera.GetInfo();
            if (camera == null || !camera.Connected) return;
            if (camera.XSize > 0 && camera.YSize > 0 && (camera.XSize != settings.SensorWidth || camera.YSize != settings.SensorHeight)) {
                settings.SensorWidth = camera.XSize;
                settings.SensorHeight = camera.YSize;
            }
            if (camera.PixelSize > 0 && Math.Abs(camera.PixelSize - settings.PixelSize) > 1e-6) settings.PixelSize = camera.PixelSize;
            var colour = camera.SensorType != SensorType.Monochrome;
            if (colour != settings.Colour) settings.Colour = colour;
        }

        public static bool HasRotator(HostServices host) {
            try {
                return host.Rotator.GetInfo()?.Connected == true;
            } catch (Exception) {
                return false;
            }
        }

        /// <summary>The filters this rig has, by the wheel's spelling, with the bandpass where the name says it.</summary>
        public static List<string> FilterNames(StarfrontSettings settings) {
            var names = settings.ProfileFilterNames();
            if (names.Count == 0) names.Add(settings.Colour ? "RGB" : "none");
            return names;
        }

        public static JObject Build(HostServices host, StarfrontSettings settings, FrameLedger ledger) {
            RememberCamera(host, settings);
            var profile = host.Profile.ActiveProfile;
            var focal = profile.TelescopeSettings.FocalLength;
            var pixel = settings.PixelSize > 0 ? settings.PixelSize : profile.CameraSettings.PixelSize;

            var filters = new JObject();
            // What a sub through each filter delivers, as chosen in the
            // options or read from the name: the server deals and credits
            // by channel, so a colour camera's L-eXtreme is Ha and OIII.
            var channels = new JObject();
            foreach (var name in FilterNames(settings)) {
                var bandpass = Filters.Bandpass(name);
                filters[name] = bandpass.HasValue ? new JValue(bandpass.Value) : JValue.CreateNull();
                channels[name] = new JArray(settings.ChannelsFor(name));
            }
            var exposures = new JObject();
            foreach (var kv in settings.Exposures) exposures[kv.Key] = kv.Value;

            var rotation = HasRotator(host) ? JValue.CreateNull()
                : new JValue(double.IsNaN(settings.CameraAngle) ? 0.0 : Math.Round(settings.CameraAngle, 2));
            var (sensorWidth, sensorHeight) = Sensor(host, settings);

            var body = new JObject {
                ["name"] = settings.TelescopeName,
                ["focalLength"] = focal > 0 ? new JValue(focal) : JValue.CreateNull(),
                ["pixelSize"] = pixel > 0 ? new JValue(pixel) : JValue.CreateNull(),
                ["sensorWidth"] = sensorWidth > 0 ? new JValue(sensorWidth) : JValue.CreateNull(),
                ["sensorHeight"] = sensorHeight > 0 ? new JValue(sensorHeight) : JValue.CreateNull(),
                ["binning"] = (int)settings.Binning,
                ["filters"] = filters,
                ["channels"] = channels,
                ["colour"] = settings.Colour,
                ["typicalHfr"] = Number(ledger.TypicalHfr()),
                ["typicalGuideRms"] = Number(ledger.TypicalGuideRms()),
                ["exposures"] = exposures,
                ["rotation"] = rotation,
                // How much of a night this telescope gives is the sequence's
                // business, not a setting: the server sizes the deal to its
                // default and the sequence shoots as much of it as it has time for.
                ["hoursPerNight"] = JValue.CreateNull(),
                ["bortle"] = settings.Bortle > 0 ? new JValue(settings.Bortle) : JValue.CreateNull(),
                ["windowFrom"] = "",
                ["windowTo"] = "",
                ["client"] = "nina",
            };
            return body;
        }

        /// <summary>Where this telescope points and what it is doing, for the others' charts. Never the site's location.</summary>
        public static JObject Presence(HostServices host, StarfrontSettings settings, RunState run) {
            var said = new JObject { ["telescope"] = Truncate(settings.TelescopeName, 60) };
            var state = "idle";
            try {
                var mount = host.Telescope.GetInfo();
                if (mount != null && mount.Connected) {
                    var coordinates = mount.Coordinates?.Transform(NINA.Astrometry.Epoch.J2000);
                    if (coordinates != null) {
                        said["ra"] = Math.Round(coordinates.RA, 5);
                        said["dec"] = Math.Round(coordinates.Dec, 4);
                    }
                    if (mount.AtPark) state = "parked";
                    else if (mount.Slewing) state = "slewing";
                }
            } catch (Exception ex) {
                Logger.Debug($"Starfront: mount not readable for presence - {ex.Message}");
            }
            if (run != null && run.Running) {
                state = "imaging";
                said["target"] = Truncate(run.TargetName ?? "", 80);
                if (!string.IsNullOrEmpty(run.ProjectId)) said["project"] = run.ProjectId;
            }
            said["state"] = state;
            return said;
        }

        private static JToken Number(double? value) => value.HasValue ? new JValue(value.Value) : JValue.CreateNull();

        private static string Truncate(string text, int length) => text.Length <= length ? text : text.Substring(0, length);
    }

    /// <summary>One field or panel of tonight's run, as the Imaging tab shows it.</summary>
    public sealed record RunItem(string Name, string Frames, string State, string Detail);

    /// <summary>What the sequence container is doing right now, for presence and the dock panel.</summary>
    public sealed class RunState : BaseINPC {
        private bool running;
        private string targetName;
        private string projectId;
        private string summary = "";
        private IReadOnlyList<RunItem> plan = Array.Empty<RunItem>();

        public bool Running { get => running; set { running = value; RaisePropertyChanged(); } }
        public string TargetName { get => targetName; set { targetName = value; RaisePropertyChanged(); } }
        public string ProjectId { get => projectId; set { projectId = value; RaisePropertyChanged(); } }
        public string Summary { get => summary; set { summary = value ?? ""; RaisePropertyChanged(); } }

        /// <summary>Tonight's fields in the order they are shot, each with where it has got to. Kept after the run ends until the next begins.</summary>
        public IReadOnlyList<RunItem> Plan { get => plan; set { plan = value ?? Array.Empty<RunItem>(); RaisePropertyChanged(); } }
    }
}
