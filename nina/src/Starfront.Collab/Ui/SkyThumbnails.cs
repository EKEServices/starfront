using System.Collections.Concurrent;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using NINA.Core.Utility;
using Starfront.Collab.Server;

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
