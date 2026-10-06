using System.Collections.ObjectModel;
using System.Globalization;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using Starfront.Collab.Settings;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Ui {

    /// <summary>One filter and the sub length this telescope shoots it at.</summary>
    public sealed class FilterExposureRow : BaseINPC {
        private readonly StarfrontSettings settings;
        private string seconds;

        public FilterExposureRow(StarfrontSettings settings, string name, double? value) {
            this.settings = settings;
            Name = name;
            seconds = value.HasValue ? value.Value.ToString("0.###", CultureInfo.InvariantCulture) : "";
        }

        public string Name { get; }

        /// <summary>What the server will call it: "Ha 3nm" counts as Ha.</summary>
        public string CountsAs {
            get {
                var canonical = Filters.Canonical(Name);
                var label = Filters.Label(canonical);
                return string.Equals(label, Name, StringComparison.OrdinalIgnoreCase) ? "" : $"counts as {label}";
            }
        }

        /// <summary>Text, so that a half-typed number is not coerced into something else; blank means unset.</summary>
        public string Seconds {
            get => seconds;
            set {
                var text = (value ?? "").Trim();
                if (text.Length == 0) {
                    seconds = "";
                    settings.SetExposure(Name, null);
                } else if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)
                           || double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out parsed)) {
                    if (parsed <= 0 || parsed > 3600) throw new ArgumentException("An exposure is between 0 and 3600 seconds.");
                    seconds = parsed.ToString("0.###", CultureInfo.InvariantCulture);
                    settings.SetExposure(Name, parsed);
                } else {
                    throw new ArgumentException("Seconds, as a number.");
                }
                RaisePropertyChanged();
            }
        }
    }

    /// <summary>The exposure table on the options page: one row per filter the profile knows.</summary>
    public sealed class ExposureTableVM : BaseINPC, IDisposable {
        private readonly StarfrontSettings settings;
        private readonly IProfileService profiles;

        public ExposureTableVM(StarfrontSettings settings, IProfileService profiles) {
            this.settings = settings;
            this.profiles = profiles;
            Rows = new ObservableCollection<FilterExposureRow>();
            Rebuild();
            profiles.ProfileChanged += OnProfileChanged;
        }

        public ObservableCollection<FilterExposureRow> Rows { get; }

        public string Hint => Rows.Count == 0
            ? "No filters are set up in this profile yet."
            : "The length you normally shoot each filter at. Your share of a collaboration is dealt at these, so every light calibrates against your own darks.";

        private void OnProfileChanged(object sender, EventArgs e) => Dispatch.OnUi(Rebuild);

        public void Rebuild() {
            var table = settings.Exposures;
            var names = settings.ProfileFilterNames();
            if (names.Count == 0) names.Add(settings.Colour ? "RGB" : "none");
            foreach (var extra in table.Keys.Where(k => !names.Contains(k, StringComparer.OrdinalIgnoreCase)).OrderBy(k => k)) names.Add(extra);
            Rows.Clear();
            foreach (var name in names) {
                table.TryGetValue(name, out var value);
                Rows.Add(new FilterExposureRow(settings, name, value > 0 ? value : null));
            }
            RaisePropertyChanged(nameof(Rows));
            RaisePropertyChanged(nameof(Hint));
        }

        public void Dispose() {
            profiles.ProfileChanged -= OnProfileChanged;
        }
    }
}
