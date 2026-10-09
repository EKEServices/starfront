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
                // N.I.N.A. answers an unknown key with "MISSING LABEL <key>"
                // rather than nothing, and that is what one tester saw on
                // the drift trigger inside their field.
                return string.IsNullOrWhiteSpace(text) || text == key
                    || text.StartsWith("MISSING LABEL", StringComparison.Ordinal) ? fallback : text;
            } catch (Exception) {
                return fallback;
            }
        }
    }
}
