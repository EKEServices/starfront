using System.Windows;
using System.Windows.Media;
using NINA.Core.Locale;

namespace Starfront.Collab.Sequencer {

    /// <summary>The host's icons and labels, for instructions the plugin builds by hand rather than through the palette.</summary>
    internal static class Icons {
        public static GeometryGroup Find(string key) {
            try {
                return Application.Current?.Resources[key] as GeometryGroup;
            } catch (Exception) {
                return null;
            }
        }

        public static string Label(string key, string fallback) {
            try {
                var text = Loc.Instance[key];
                return string.IsNullOrWhiteSpace(text) || text == key ? fallback : text;
            } catch (Exception) {
                return fallback;
            }
        }
    }
}
