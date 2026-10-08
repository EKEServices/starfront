using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using NINA.Astrometry;
using NINA.Astrometry.Interfaces;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using Starfront.Collab.Server;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Ui {

    /// <summary>
    /// A picture of a collaboration's sky: a DSS2 colour cutout from CDS's
    /// hips2fits service - the same one Starfront's planner draws on - with the
    /// project's region outlined and, for a telescope that has joined, its own
    /// panels drawn over it and tonight's share filled in. So that somebody
    /// choosing which collaboration to join can see what it is, not read four
    /// numbers describing a rectangle.
    ///
    /// Cutouts are kept on disk under the plugin's folder, so a collaboration
    /// looked at once still shows its sky with the network unplugged.
    /// </summary>
    public sealed class SkyThumbnails {
        private const string Endpoint = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits";
        private const string Survey = "CDS/P/DSS2/color";
        private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(40) };

        private readonly string cacheFolder;
        private readonly ConcurrentDictionary<string, Task<BitmapSource>> inFlight = new();

        public SkyThumbnails(string dataFolder) {
            cacheFolder = Path.Combine(dataFolder, "survey");
            Http.DefaultRequestHeaders.UserAgent.TryParseAdd("Starfront-NINA");
        }

        /// <summary>Field of view (the picture's width on the sky) that shows the whole region with a margin.</summary>
        public static double FieldFor(Region region, int width, int height) {
            var aspect = width / (double)Math.Max(1, height);
            var need = Math.Max(Math.Abs(region.Width), Math.Abs(region.Height) * aspect) * 1.3;
            return Math.Max(0.2, Math.Min(60.0, need));
        }

        /// <summary>The bare cutout, from the cache or the service; null when neither has it.</summary>
        public Task<BitmapSource> CutoutAsync(Region region, int width, int height) {
            var fov = FieldFor(region, width, height);
            var key = Key(region.Ra, region.Dec, fov, width, height);
            return inFlight.GetOrAdd(key, _ => FetchAsync(region, fov, width, height, key));
        }

        private static string Key(double ra, double dec, double fov, int width, int height) {
            var canonical = string.Create(CultureInfo.InvariantCulture, $"{ra:0.00000}|{dec:0.00000}|{fov:0.00000}|{width}|{height}|{Survey}");
            var hash = SHA256.HashData(Encoding.UTF8.GetBytes(canonical));
            return Convert.ToHexString(hash).Substring(0, 32).ToLowerInvariant();
        }

        private async Task<BitmapSource> FetchAsync(Region region, double fov, int width, int height, string key) {
            try {
                Directory.CreateDirectory(cacheFolder);
                var path = Path.Combine(cacheFolder, key + ".jpg");
                byte[] bytes = null;
                if (File.Exists(path)) {
                    bytes = await File.ReadAllBytesAsync(path).ConfigureAwait(false);
                } else {
                    var query = string.Create(CultureInfo.InvariantCulture,
                        $"hips={Uri.EscapeDataString(Survey)}&ra={region.Ra:0.000000}&dec={region.Dec:0.000000}&fov={fov:0.000000}" +
                        $"&width={width}&height={height}&rotation_angle=0&projection=TAN&coordsys=icrs&format=jpg");
                    bytes = await Http.GetByteArrayAsync($"{Endpoint}?{query}").ConfigureAwait(false);
                    if (bytes == null || bytes.Length < 4 || bytes[0] != 0xFF || bytes[1] != 0xD8) return null;
                    try { await File.WriteAllBytesAsync(path, bytes).ConfigureAwait(false); } catch (IOException) { }
                }
                var image = new BitmapImage();
                using (var stream = new MemoryStream(bytes)) {
                    image.BeginInit();
                    image.CacheOption = BitmapCacheOption.OnLoad;
                    image.StreamSource = stream;
                    image.EndInit();
                }
                image.Freeze();
                return image;
            } catch (Exception ex) {
                Logger.Warning($"Starfront: no sky picture for the collaboration at {region.Ra:0.00} {region.Dec:0.00} - {ex.Message}");
                inFlight.TryRemove(key, out _);
                return null;
            }
        }

        /// <summary>
        /// The cutout with the region outlined and the telescope's cells drawn
        /// on it, tonight's share filled. Must run on the UI thread: it draws.
        /// </summary>
        public static BitmapSource Compose(BitmapSource cutout, Region region, IReadOnlyList<Cell> cells, IReadOnlyCollection<int> share, int width, int height) {
            var fov = FieldFor(region, width, height);
            var scale = fov / width;                       // degrees per pixel
            var visual = new DrawingVisual();
            using (var dc = visual.RenderOpen()) {
                dc.DrawRectangle(Brushes.Black, null, new Rect(0, 0, width, height));
                if (cutout != null) dc.DrawImage(cutout, new Rect(0, 0, width, height));

                Point[] Corners(double ra, double dec, double w, double h, double rotation) {
                    var (xi0, eta0) = Tangent(region.Ra, region.Dec, ra, dec);
                    var angle = rotation * Math.PI / 180.0;
                    var cos = Math.Cos(angle);
                    var sin = Math.Sin(angle);
                    var points = new Point[4];
                    var k = 0;
                    foreach (var (x, y) in new[] { (-w / 2, h / 2), (w / 2, h / 2), (w / 2, -h / 2), (-w / 2, -h / 2) }) {
                        // Camera-frame offset to (east, north), as the planner does it.
                        var east = -x * cos + y * sin;
                        var north = x * sin + y * cos;
                        points[k++] = new Point(width / 2.0 - (xi0 + east) / scale, height / 2.0 - (eta0 + north) / scale);
                    }
                    return points;
                }
                void Polygon(Point[] points, Pen pen, Brush fill) {
                    var geometry = new StreamGeometry();
                    using (var g = geometry.Open()) {
                        g.BeginFigure(points[0], fill != null, true);
                        g.PolyLineTo(points.Skip(1).ToList(), true, true);
                    }
                    geometry.Freeze();
                    dc.DrawGeometry(fill, pen, geometry);
                }

                if (cells != null && cells.Count > 0) {
                    var cellPen = new Pen(new SolidColorBrush(Color.FromArgb(150, 255, 255, 255)), 1.0);
                    var sharePen = new Pen(new SolidColorBrush(Color.FromArgb(230, 126, 231, 165)), 1.5);
                    var shareFill = new SolidColorBrush(Color.FromArgb(70, 126, 231, 165));
                    cellPen.Freeze(); sharePen.Freeze(); shareFill.Freeze();
                    for (var i = 0; i < cells.Count; i++) {
                        var cell = cells[i];
                        var mine = share != null && share.Contains(i);
                        Polygon(Corners(cell.Ra, cell.Dec, cell.Width, cell.Height, cell.Rotation), mine ? sharePen : cellPen, mine ? shareFill : null);
                    }
                }
                var regionPen = new Pen(new SolidColorBrush(Color.FromArgb(230, 255, 196, 0)), 2.0) { DashStyle = DashStyles.Dash };
                regionPen.Freeze();
                Polygon(Corners(region.Ra, region.Dec, region.Width, region.Height, 0.0), regionPen, null);
            }
            var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(visual);
            bitmap.Freeze();
            return bitmap;
        }

        /// <summary>
        /// The target's altitude through tonight, as a small chart: dusk to
        /// dawn across, altitude up, the horizon and the project's floor
        /// shaded, the curve, a mark at transit and a line at "now". Answers
        /// "how well placed is this tonight?" before a word is read. Must run
        /// on the UI thread: it draws. Returns the picture and one line of
        /// words saying the same thing.
        /// </summary>
        public static (BitmapSource image, string caption) Transit(Region region, IProfileService profiles, INighttimeCalculator calculator,
                                                                    double minAltitude, int width, int height) {
            var coordinates = new Coordinates(Angle.ByDegree(region.Ra), Angle.ByDegree(region.Dec), Epoch.J2000);
            DateTime dusk, dawn;
            try {
                (dusk, dawn) = SkyMath.DarkHours(calculator.Calculate());
            } catch (Exception) {
                dusk = DateTime.Now.Date.AddHours(20);
                dawn = dusk.AddHours(10);
            }
            var now = DateTime.Now;
            // Tonight's dark, or last night's still being shown in the morning:
            // the chart reads from an hour before dusk to an hour after dawn.
            if (dawn < now.AddHours(-2)) { dusk = dusk.AddDays(1); dawn = dawn.AddDays(1); }
            var start = dusk.AddHours(-1);
            var end = dawn.AddHours(1);
            var span = (end - start).TotalMinutes;
            if (span <= 0) span = 1;

            const int left = 30, right = 6, top = 14, bottom = 16;
            var plotWidth = width - left - right;
            var plotHeight = height - top - bottom;
            double X(DateTime t) => left + (t - start).TotalMinutes / span * plotWidth;
            double Y(double altitude) => top + (1.0 - Math.Max(0.0, Math.Min(90.0, altitude)) / 90.0) * plotHeight;

            var points = new List<(DateTime t, double alt, double floor)>();
            DateTime? transitAt = null;
            var best = -90.0;
            DateTime? upFrom = null, upTo = null;
            for (var t = start; t <= end; t = t.AddMinutes(5)) {
                var (altitude, azimuth) = SkyMath.AltAz(coordinates, profiles, t);
                var floor = Math.Max(0.0, SkyMath.HorizonAltitude(profiles, azimuth));
                points.Add((t, altitude, floor));
                if (altitude > best) { best = altitude; transitAt = t; }
                var usable = altitude >= Math.Max(minAltitude, floor) && t >= dusk && t <= dawn;
                if (usable) { upFrom ??= t; upTo = t; }
            }

            var visual = new DrawingVisual();
            using (var dc = visual.RenderOpen()) {
                dc.DrawRectangle(new SolidColorBrush(Color.FromRgb(8, 11, 20)), null, new Rect(0, 0, width, height));
                // The dark itself, a shade lighter than the twilight either side.
                var night = new SolidColorBrush(Color.FromRgb(14, 19, 34));
                night.Freeze();
                dc.DrawRectangle(night, null, new Rect(X(dusk), top, Math.Max(1, X(dawn) - X(dusk)), plotHeight));
                // Ground: the profile's horizon, or zero without one.
                var ground = new StreamGeometry();
                using (var g = ground.Open()) {
                    g.BeginFigure(new Point(X(points[0].t), Y(0)), true, true);
                    foreach (var p in points) g.LineTo(new Point(X(p.t), Y(p.floor)), false, false);
                    g.LineTo(new Point(X(points[^1].t), Y(0)), false, false);
                }
                ground.Freeze();
                var groundBrush = new SolidColorBrush(Color.FromArgb(160, 60, 48, 36));
                groundBrush.Freeze();
                dc.DrawGeometry(groundBrush, null, ground);
                // The project's floor, below which a frame does not count.
                var floorPen = new Pen(new SolidColorBrush(Color.FromArgb(140, 240, 179, 91)), 1.0) { DashStyle = DashStyles.Dash };
                floorPen.Freeze();
                if (minAltitude > 0) dc.DrawLine(floorPen, new Point(left, Y(minAltitude)), new Point(width - right, Y(minAltitude)));
                // Gridlines at 30 and 60 degrees, faint.
                var gridPen = new Pen(new SolidColorBrush(Color.FromArgb(40, 160, 180, 220)), 1.0);
                gridPen.Freeze();
                foreach (var alt in new[] { 30.0, 60.0 }) dc.DrawLine(gridPen, new Point(left, Y(alt)), new Point(width - right, Y(alt)));
                // The part of the night the target is usable, filled under the curve.
                if (upFrom != null && upTo != null) {
                    var usable = new StreamGeometry();
                    using (var g = usable.Open()) {
                        var inside = points.Where(p => p.t >= upFrom && p.t <= upTo).ToList();
                        g.BeginFigure(new Point(X(inside[0].t), Y(Math.Max(minAltitude, inside[0].floor))), true, true);
                        foreach (var p in inside) g.LineTo(new Point(X(p.t), Y(p.alt)), false, false);
                        for (var i = inside.Count - 1; i >= 0; i--) g.LineTo(new Point(X(inside[i].t), Y(Math.Max(minAltitude, inside[i].floor))), false, false);
                    }
                    usable.Freeze();
                    var usableBrush = new SolidColorBrush(Color.FromArgb(70, 126, 231, 165));
                    usableBrush.Freeze();
                    dc.DrawGeometry(usableBrush, null, usable);
                }
                // The curve.
                var curve = new StreamGeometry();
                using (var g = curve.Open()) {
                    g.BeginFigure(new Point(X(points[0].t), Y(points[0].alt)), false, false);
                    g.PolyLineTo(points.Skip(1).Select(p => new Point(X(p.t), Y(p.alt))).ToList(), true, true);
                }
                curve.Freeze();
                var curvePen = new Pen(new SolidColorBrush(Color.FromRgb(126, 231, 165)), 1.8);
                curvePen.Freeze();
                dc.DrawGeometry(null, curvePen, curve);
                // Now.
                if (now >= start && now <= end) {
                    var nowPen = new Pen(new SolidColorBrush(Color.FromArgb(200, 255, 255, 255)), 1.0);
                    nowPen.Freeze();
                    dc.DrawLine(nowPen, new Point(X(now), top), new Point(X(now), top + plotHeight));
                }
                // Transit.
                if (transitAt != null && best > 0) {
                    var mark = new SolidColorBrush(Color.FromRgb(255, 196, 0));
                    mark.Freeze();
                    dc.DrawEllipse(mark, null, new Point(X(transitAt.Value), Y(best)), 3, 3);
                }
                // Words: the hours along the bottom, the altitude up the side.
                var typeface = new Typeface("Segoe UI");
                var ink = new SolidColorBrush(Color.FromArgb(190, 220, 228, 240));
                ink.Freeze();
                void Label(string text, double x, double y, TextAlignment align = TextAlignment.Left) {
                    var formatted = new FormattedText(text, CultureInfo.CurrentCulture, FlowDirection.LeftToRight, typeface, 9.5, ink, 1.0) { TextAlignment = align };
                    dc.DrawText(formatted, new Point(x, y));
                }
                Label("90°", left - 4, top - 6, TextAlignment.Right);
                Label("0°", left - 4, Y(0) - 6, TextAlignment.Right);
                if (minAltitude > 0) Label($"{minAltitude:0}°", left - 4, Y(minAltitude) - 6, TextAlignment.Right);
                for (var t = new DateTime(start.Year, start.Month, start.Day, start.Hour, 0, 0).AddHours(1); t < end; t = t.AddHours(2)) {
                    Label(t.ToString("HH:mm"), X(t), height - bottom + 2, TextAlignment.Center);
                }
                if (transitAt != null && best > 0) {
                    var x = Math.Max(left + 24, Math.Min(width - right - 24, X(transitAt.Value)));
                    Label($"transit {transitAt.Value:HH:mm} · {best:0}°", x, top - 12, TextAlignment.Center);
                }
            }
            var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(visual);
            bitmap.Freeze();

            string caption;
            if (upFrom == null || upTo == null) {
                caption = best <= 0 ? "never rises tonight" : $"never above {minAltitude:0}° during the dark tonight (peaks at {best:0}°)";
            } else {
                var hours = (upTo.Value - upFrom.Value).TotalHours;
                caption = $"above {minAltitude:0}° from {upFrom:HH:mm} to {upTo:HH:mm} ({hours:0.0} h) · transit {transitAt:HH:mm} at {best:0}°";
            }
            return (bitmap, caption);
        }

        /// <summary>Gnomonic standard coordinates of (ra, dec) about (ra0, dec0), in degrees, east and north positive.</summary>
        private static (double xi, double eta) Tangent(double ra0Deg, double dec0Deg, double raDeg, double decDeg) {
            var ra0 = ra0Deg * Math.PI / 180.0;
            var dec0 = dec0Deg * Math.PI / 180.0;
            var ra = raDeg * Math.PI / 180.0;
            var dec = decDeg * Math.PI / 180.0;
            var dRa = ra - ra0;
            var denominator = Math.Sin(dec) * Math.Sin(dec0) + Math.Cos(dec) * Math.Cos(dec0) * Math.Cos(dRa);
            if (denominator <= 1e-9) return (0.0, 0.0);
            var xi = Math.Cos(dec) * Math.Sin(dRa) / denominator;
            var eta = (Math.Sin(dec) * Math.Cos(dec0) - Math.Cos(dec) * Math.Sin(dec0) * Math.Cos(dRa)) / denominator;
            return (xi * 180.0 / Math.PI, eta * 180.0 / Math.PI);
        }
    }
}
