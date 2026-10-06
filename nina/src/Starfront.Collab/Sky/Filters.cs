using System.Globalization;
using System.Text.RegularExpressions;

namespace Starfront.Collab.Sky {

    /// <summary>
    /// Filter names as the server compares them: the same table as the
    /// server's own, so "Ha", "H-alpha" and "Ha 3nm" are one filter here
    /// too and a wheel can spell its filters however it likes.
    /// </summary>
    public static class Filters {
        private static readonly Dictionary<string, string> Lookup = Build(new Dictionary<string, string[]> {
            ["L"] = new[] { "l", "lum", "luminance", "luminanz", "lumi", "clear", "c", "uvir", "uv/ir", "uvircut", "ircut", "irc", "ir/uv", "white", "none" },
            ["R"] = new[] { "r", "red", "rot", "rouge" },
            ["G"] = new[] { "g", "green", "grn", "gruen", "grün", "vert" },
            ["B"] = new[] { "b", "blue", "blau", "bleu" },
            ["H"] = new[] { "h", "ha", "halpha", "hα", "hɑ", "ha3", "hydrogen", "hydrogenalpha", "hyd", "656" },
            ["O"] = new[] { "o", "oiii", "o3", "oxygen", "oxygeniii", "o111", "501" },
            ["S"] = new[] { "s", "sii", "s2", "sulfur", "sulphur", "sulfurii", "sulphurii", "s11", "672" },
        });

        private static readonly Regex BandpassSuffix = new(@"[\s(\[-]*\d+(?:\.\d+)?\s*nm[)\]]*\s*$", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly Regex BandpassValue = new(@"(\d+(?:\.\d+)?)\s*nm", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly Regex Separators = new(@"[\s_\-]+", RegexOptions.Compiled);
        private static readonly Regex Numerals = new(@"(iii|ii|i)$", RegexOptions.Compiled);

        private static Dictionary<string, string> Build(Dictionary<string, string[]> aliases) {
            var table = new Dictionary<string, string>();
            foreach (var kv in aliases) foreach (var alias in kv.Value) table[alias] = kv.Key;
            return table;
        }

        /// <summary>"Ha" → "H", "luminance" → "L", "OIII 3nm" → "O"; anything else stays as typed.</summary>
        public static string Canonical(string name) {
            var text = string.Join(" ", (name ?? "").Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
            if (text.Length > 24) text = text.Substring(0, 24);
            if (text.Length == 0) return "";
            var bare = BandpassSuffix.Replace(text, "").Trim();
            var key = Separators.Replace(bare, "").ToLowerInvariant();
            if (Lookup.TryGetValue(key, out var letter)) return letter;
            var key2 = Numerals.Replace(key, "");
            if (key2.Length > 0 && (key2 == "h" || key2 == "o" || key2 == "s") && Lookup.TryGetValue(key2, out letter)) return letter;
            return text;
        }

        public static bool Same(string a, string b) =>
            string.Equals(Canonical(a), Canonical(b), StringComparison.OrdinalIgnoreCase);

        /// <summary>The bandpass written into a name - "Ha 3nm" - in nanometres, or null.</summary>
        public static double? Bandpass(string name) {
            var match = BandpassValue.Match(name ?? "");
            if (!match.Success) return null;
            return double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : null;
        }

        /// <summary>A friendly label for a canonical letter.</summary>
        public static string Label(string canonical) => canonical switch {
            "H" => "Ha",
            "O" => "OIII",
            "S" => "SII",
            _ => canonical ?? "",
        };
    }
}
