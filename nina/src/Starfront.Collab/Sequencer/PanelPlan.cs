using NINA.Astrometry;
using Starfront.Collab.Server;

namespace Starfront.Collab.Sequencer {

    /// <summary>Frames of one filter at one exposure.</summary>
    public sealed record FilterFrames(string Filter, string WheelName, int Frames, double Exposure);

    /// <summary>
    /// One panel of tonight's deal, as the container will shoot it: where,
    /// which filters and how many frames, and the rules the project set.
    /// </summary>
    public sealed class PanelPlan {
        public DealTask Deal { get; init; }
        public int CellIndex { get; init; }
        public int PanelNumber => CellIndex + 1;
        public Cell Cell { get; init; }
        public Coordinates Coordinates { get; init; }
        public List<FilterFrames> Filters { get; init; } = new();
        public double MinAltitude { get; init; }
        public double? MinMoonSeparation { get; init; }
        public string TargetName { get; init; } = "";

        /// <summary>Seconds the panel takes, exposures only.</summary>
        public double Seconds => Filters.Sum(f => f.Frames * f.Exposure);

        /// <summary>"12 × Ha 300s", with the filter as the wheel spells it.</summary>
        public string FramesText => string.Join(", ", Filters.Select(f => $"{f.Frames} × {f.WheelName ?? f.Filter} {f.Exposure:0.#}s"));

        public bool SingleField => Deal?.Kind == "single";

        /// <summary>Hours from now until the panel drops below the floor, or null if it never rises above it in the next twelve.</summary>
        public double? HoursAbove { get; set; }

        /// <summary>Hours from now until the panel first rises above the floor; zero when it is up now.</summary>
        public double? HoursUntilUp { get; set; }
    }
}
