using System.IO;
using Newtonsoft.Json;
using NINA.Core.Utility;
using Starfront.Collab.Server;

namespace Starfront.Collab.Ledger {

    /// <summary>One night's frames through one filter on one panel of one task, as measured.</summary>
    public sealed class LedgerRow {
        public string Night { get; set; } = "";
        public string Project { get; set; } = "";
        public string Task { get; set; } = "";
        public string Panel { get; set; } = "";
        public string Filter { get; set; } = "";
        public int Frames { get; set; }
        public double Seconds { get; set; }
        public double HfrSum { get; set; }
        public int HfrCount { get; set; }
        public double RmsSum { get; set; }
        public int RmsCount { get; set; }
        public Region Footprint { get; set; }
        public double? Scale { get; set; }
        public double? FocalLength { get; set; }
        public bool Colour { get; set; }
        public double? Bandpass { get; set; }
        public bool Reported { get; set; }
        public int ReportedFrames { get; set; }
        public DateTime LastFrameUtc { get; set; }

        [JsonIgnore] public string Key => MakeKey(Night, Task, Panel, Filter);
        public static string MakeKey(string night, string task, string panel, string filter) => $"{night}|{task}|{panel}|{filter}";

        [JsonIgnore] public double? MeanHfr => HfrCount > 0 ? HfrSum / HfrCount : null;
        [JsonIgnore] public double? MeanRms => RmsCount > 0 ? RmsSum / RmsCount : null;
    }

    /// <summary>
    /// What this telescope has really shot for its collaborations, kept on
    /// disk so that a night the server did not take - it was down, the
    /// connection was - is sent tomorrow and nothing is lost. One row per
    /// night, task, panel and filter, because depth is per point on the sky
    /// and a mosaic's panels are different sky.
    /// </summary>
    public sealed class FrameLedger {
        private readonly string path;
        private readonly object gate = new();
        private Dictionary<string, LedgerRow> rows = new();

        public FrameLedger(string path) {
            this.path = path;
        }

        public event EventHandler Changed;

        public void Load() {
            lock (gate) {
                try {
                    if (File.Exists(path)) {
                        var list = JsonConvert.DeserializeObject<List<LedgerRow>>(File.ReadAllText(path)) ?? new List<LedgerRow>();
                        rows = list.Where(r => r != null).GroupBy(r => r.Key).ToDictionary(g => g.Key, g => g.Last());
                    }
                } catch (Exception ex) {
                    Logger.Error("Starfront: the frame ledger could not be read; starting afresh", ex);
                    try { File.Copy(path, path + ".broken", true); } catch (Exception) { }
                    rows = new Dictionary<string, LedgerRow>();
                }
            }
        }

        public void Save() {
            lock (gate) {
                try {
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    var temporary = path + ".tmp";
                    File.WriteAllText(temporary, JsonConvert.SerializeObject(rows.Values.OrderBy(r => r.Night).ThenBy(r => r.Panel).ToList(), Formatting.Indented));
                    File.Move(temporary, path, true);
                } catch (Exception ex) {
                    Logger.Error("Starfront: the frame ledger could not be written", ex);
                }
            }
        }

        /// <summary>Add one frame. A row already reported is re-opened: the server replaces a panel's row at the larger figure.</summary>
        public void Credit(string night, string project, string task, string panel, string filter, double seconds,
                           double? hfrArcsec, double? rmsArcsec, Region footprint, double? scale, double? focalLength,
                           bool colour, double? bandpass) {
            if (seconds <= 0) return;
            lock (gate) {
                var key = LedgerRow.MakeKey(night, task, panel, filter);
                if (!rows.TryGetValue(key, out var row)) {
                    row = new LedgerRow { Night = night, Project = project, Task = task, Panel = panel, Filter = filter };
                    rows[key] = row;
                }
                row.Frames++;
                row.Seconds += seconds;
                if (hfrArcsec is double hfr && hfr > 0 && !double.IsNaN(hfr) && !double.IsInfinity(hfr)) { row.HfrSum += hfr; row.HfrCount++; }
                if (rmsArcsec is double rms && rms > 0 && !double.IsNaN(rms) && !double.IsInfinity(rms)) { row.RmsSum += rms; row.RmsCount++; }
                if (footprint != null) row.Footprint = footprint;
                if (scale.HasValue) row.Scale = scale;
                if (focalLength.HasValue) row.FocalLength = focalLength;
                row.Colour = colour;
                if (bandpass.HasValue) row.Bandpass = bandpass;
                row.Reported = false;
                row.LastFrameUtc = DateTime.UtcNow;
            }
            Save();
            Changed?.Invoke(this, EventArgs.Empty);
        }

        public List<LedgerRow> Unreported() {
            lock (gate) {
                return rows.Values.Where(r => !r.Reported && r.Frames > 0).Select(Copy).ToList();
            }
        }

        public void MarkReported(string key) {
            lock (gate) {
                if (rows.TryGetValue(key, out var row)) {
                    row.Reported = true;
                    row.ReportedFrames = row.Frames;
                }
            }
            Save();
        }

        public List<LedgerRow> Rows(string night = null) {
            lock (gate) {
                return rows.Values.Where(r => night == null || r.Night == night).Select(Copy).ToList();
            }
        }

        /// <summary>Median star size this telescope achieves, in arcseconds, over its last thirty nights with a measurement.</summary>
        public double? TypicalHfr() => Median(rows.Values.Where(r => r.HfrCount > 0).GroupBy(r => r.Night)
            .OrderByDescending(g => g.Key).Take(30).Select(g => g.Sum(r => r.HfrSum) / g.Sum(r => r.HfrCount)));

        public double? TypicalGuideRms() => Median(rows.Values.Where(r => r.RmsCount > 0).GroupBy(r => r.Night)
            .OrderByDescending(g => g.Key).Take(30).Select(g => g.Sum(r => r.RmsSum) / g.Sum(r => r.RmsCount)));

        private static double? Median(IEnumerable<double> values) {
            var sorted = values.Where(v => v > 0).OrderBy(v => v).ToList();
            if (sorted.Count == 0) return null;
            var middle = sorted.Count / 2;
            var value = sorted.Count % 2 == 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2.0;
            return Math.Round(value, 2);
        }

        private static LedgerRow Copy(LedgerRow row) => JsonConvert.DeserializeObject<LedgerRow>(JsonConvert.SerializeObject(row));
    }
}
