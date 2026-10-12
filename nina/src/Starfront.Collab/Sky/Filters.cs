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

        /// <summary>The seven channels a collaboration can ask for, in the order they are shown.</summary>
        public static readonly string[] Channels = { "L", "R", "G", "B", "H", "O", "S" };

        /// <summary>
        /// What a filter delivers, by code: one letter for a mono filter, "RGB" for
        /// a colour camera's broadband, "HO" / "SO" / "HS" / "HOS" for the dual and
        /// tri-band filters, and "-" for a filter that gives a collaboration nothing
        /// (a dark, a diffraction grating). The same table as the server's.
        /// </summary>
        public static readonly (string Code, string Label)[] Deliveries = {
            ("L", "Luminance / clear (mono)"),
            ("R", "Red"), ("G", "Green"), ("B", "Blue"),
            ("H", "Ha"), ("O", "OIII"), ("S", "SII"),
            ("RGB", "Colour: red, green and blue in one sub (OSC, no filter or UV/IR cut)"),
            ("HO", "Ha + OIII dual-band (L-eXtreme, L-eNhance, L-Ultimate, ALP-T, NBZ, D1…)"),
            ("SO", "SII + OIII dual-band (Askar D2, NB3…)"),
            ("HS", "Ha + SII dual-band"),
            ("HOS", "Ha + OIII + SII tri/quad-band (Triad Ultra, Quad…)"),
            ("-", "Nothing a collaboration wants"),
        };

        private static readonly Dictionary<string, string> Multi = Build(new Dictionary<string, string[]> {
            ["HO"] = new[] { "lextreme", "lxtreme", "lenhance", "lultimate", "optolonglextreme", "optolonglenhance", "optolonglultimate",
                             "alpt", "antliaalpt", "duo", "dual", "duoband", "dualband", "duonarrowband", "dualnarrowband", "dualnb", "duonb",
                             "haoiii", "oiiiha", "hao3", "o3ha", "haoiii7nm", "ho", "oh", "nbz", "idasnbz", "nb1", "idasnb1", "sv220", "svbonysv220",
                             "d1", "colourmagicd1", "colormagicd1", "askard1", "askarcolourmagicd1", "zwoduoband", "zwoduo", "duoband7nm", "nebulabooster",
                             "triad", "tri", "triband" },
            ["SO"] = new[] { "d2", "colourmagicd2", "colormagicd2", "askard2", "askarcolourmagicd2", "siioiii", "oiiisii", "s2o3", "o3s2", "so", "os", "nb3", "idasnb3" },
            ["HS"] = new[] { "hasii", "siiha", "has2", "s2ha", "hs", "sh" },
            ["HOS"] = new[] { "triadultra", "quad", "quadband", "lquad", "quadnarrowband", "quadbandultra", "hos", "haoiiisii", "antliaquadband", "altairquadband", "nbx" },
        });
        private static readonly HashSet<string> Broadband = new() { "lpro", "optolonglpro", "cls", "clsccd", "uhc", "uhcs", "lps", "lpsd1", "lpsd2", "lpsp2", "lpsv4", "idaslps", "lightpollution", "lp", "lpf", "skyglow", "cityglow", "nightsky", "uvircut", "l3", "l2" };
        private static readonly HashSet<string> ColourWords = new() { "rgb", "osc", "colour", "color", "oneshotcolour", "oneshotcolor", "bayer", "nofilter", "open", "empty" };
        private static readonly Regex LooseSeparators = new(@"[\s_\-/+&().,]+", RegexOptions.Compiled);

        /// <summary>The delivery code a filter's name says it is, or "" when the name says nothing.</summary>
        public static string GuessDelivery(string name, bool colour) {
            var letter = Canonical(name);
            if (Channels.Contains(letter)) return colour && letter == "L" ? "RGB" : letter;
            var text = BandpassSuffix.Replace(name ?? "", "").Trim();
            var key = LooseSeparators.Replace(text, "").ToLowerInvariant();
            if (key.Length == 0) return "";
            if (ColourWords.Contains(key)) return "RGB";
            if (Multi.TryGetValue(key, out var code)) return code;
            if (Broadband.Contains(key)) return colour ? "RGB" : "L";
            return "";
        }

        /// <summary>
        /// The band a delivery code stands for, on this camera. A colour
        /// camera's data is its own band, kept apart from the mono channels:
        /// "RGB" → OSC-RGB, "HO" → OSC-Ha+OIII; on a mono camera "H" → H and
        /// "HO" → Ha+OIII. "-" or "" → nothing. One entry, as the server speaks it.
        /// </summary>
        public static List<string> ChannelsOf(string code, bool colour) {
            var clean = (code ?? "").Trim().ToUpperInvariant();
            if (clean.Length == 0 || clean == "-") return new List<string>();
            var letters = clean == "RGB" ? new List<string> { "R", "G", "B" }
                : clean.Select(c => c.ToString()).Where(c => Channels.Contains(c)).Distinct().ToList();
            if (letters.Count == 0) return new List<string>();
            var band = BandOf(letters, colour);
            return band.Length > 0 ? new List<string> { band } : new List<string>();
        }

        private static string BandOf(List<string> letters, bool colour) {
            var set = new HashSet<string>(letters);
            string lines;
            if (set.SetEquals(new[] { "R", "G", "B" }) || (colour && set.IsSubsetOf(new[] { "R", "G", "B", "L" }))) lines = "RGB";
            else if (set.SetEquals(new[] { "H", "O" })) lines = "Ha+OIII";
            else if (set.SetEquals(new[] { "S", "O" })) lines = "SII+OIII";
            else if (set.SetEquals(new[] { "H", "S" })) lines = "Ha+SII";
            else if (set.SetEquals(new[] { "H", "O", "S" })) lines = "Ha+OIII+SII";
            else if (set.Count == 1) lines = colour ? Label(letters[0]) : letters[0];
            else return "";
            if (colour) return "OSC-" + lines;
            if (lines == "RGB") return "L";
            return lines;
        }

        /// <summary>Whether a channel or band is one-shot colour data.</summary>
        public static bool IsOsc(string channel) => (channel ?? "").StartsWith("OSC-", StringComparison.OrdinalIgnoreCase);

        public static string DeliveryLabel(string code) {
            foreach (var (c, label) in Deliveries) if (c == code) return label;
            return code ?? "";
        }
    }
}
