using System.Collections.ObjectModel;
using System.Globalization;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using Starfront.Collab.Settings;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Ui {

    /// <summary>A choice of what a filter delivers, for the table's drop-down.</summary>
    public sealed record DeliveryChoice(string Code, string Label);

    /// <summary>One filter: what it delivers, and the sub length this telescope shoots it at.</summary>
    public sealed class FilterExposureRow : BaseINPC {
        public static readonly IReadOnlyList<DeliveryChoice> Choices =
            Filters.Deliveries.Select(d => new DeliveryChoice(d.Code, d.Label)).ToList();

        private readonly StarfrontSettings settings;
        private string seconds;
        private string delivers;

        public FilterExposureRow(StarfrontSettings settings, string name, double? value) {
            this.settings = settings;
            Name = name;
            seconds = value.HasValue ? value.Value.ToString("0.###", CultureInfo.InvariantCulture) : "";
            delivers = settings.DeliveryFor(name);
            if (delivers.Length == 0) delivers = "-";
        }

        public string Name { get; }

        /// <summary>The delivery code: what a collaboration gets from a sub through this filter.</summary>
        public string Delivers {
            get => delivers;
            set {
                var code = (value ?? "").Trim().ToUpperInvariant();
                if (code.Length == 0) code = "-";
                delivers = code;
                settings.SetDelivery(Name, code);
                RaisePropertyChanged();
            }
        }

        public IReadOnlyList<DeliveryChoice> DeliveryChoices => Choices;

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
            : "Each filter by the name in your wheel: what a collaboration gets from a sub through it, and the length you normally shoot it at. A mono Ha delivers Ha; a colour camera's UV/IR cut delivers red, green and blue in one sub; an L-eXtreme on a colour camera delivers Ha and OIII at once, and a night through it is credited to both. The guess from the name is usually right - check it. Your share of a collaboration is dealt at these lengths, so every light calibrates against your own darks.";

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
