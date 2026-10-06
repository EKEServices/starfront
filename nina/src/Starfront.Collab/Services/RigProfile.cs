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

        /// <summary>The camera's field on the sky in degrees, width by height, or null until the sensor is known.</summary>
        public static (double width, double height)? Field(HostServices host, StarfrontSettings settings) {
            var scale = Scale(host, settings);
            if (scale == null || settings.SensorWidth <= 0 || settings.SensorHeight <= 0) return null;
            var binning = Math.Max(1, (int)settings.Binning);
            return (scale.Value * (settings.SensorWidth / (double)binning) / 3600.0,
                    scale.Value * (settings.SensorHeight / (double)binning) / 3600.0);
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
            foreach (var name in FilterNames(settings)) {
                var bandpass = Filters.Bandpass(name);
                filters[name] = bandpass.HasValue ? new JValue(bandpass.Value) : JValue.CreateNull();
            }
            var exposures = new JObject();
            foreach (var kv in settings.Exposures) exposures[kv.Key] = kv.Value;

            var rotation = HasRotator(host) ? JValue.CreateNull()
                : new JValue(double.IsNaN(settings.CameraAngle) ? 0.0 : Math.Round(settings.CameraAngle, 2));

            var body = new JObject {
                ["name"] = settings.TelescopeName,
                ["focalLength"] = focal > 0 ? new JValue(focal) : JValue.CreateNull(),
                ["pixelSize"] = pixel > 0 ? new JValue(pixel) : JValue.CreateNull(),
                ["sensorWidth"] = settings.SensorWidth > 0 ? new JValue(settings.SensorWidth) : JValue.CreateNull(),
                ["sensorHeight"] = settings.SensorHeight > 0 ? new JValue(settings.SensorHeight) : JValue.CreateNull(),
                ["binning"] = (int)settings.Binning,
                ["filters"] = filters,
                ["colour"] = settings.Colour,
                ["typicalHfr"] = Number(ledger.TypicalHfr()),
                ["typicalGuideRms"] = Number(ledger.TypicalGuideRms()),
                ["exposures"] = exposures,
                ["rotation"] = rotation,
                ["hoursPerNight"] = settings.HoursPerNight > 0 ? new JValue(settings.HoursPerNight) : JValue.CreateNull(),
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
