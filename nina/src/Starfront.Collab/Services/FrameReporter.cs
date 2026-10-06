using System.Collections.Concurrent;
using NINA.Core.Utility;
using NINA.Image.ImageData;
using NINA.WPF.Base.Interfaces.Mediator;
using Starfront.Collab.Ledger;
using Starfront.Collab.Server;
using Starfront.Collab.Sky;

namespace Starfront.Collab.Services {

    /// <summary>Which collaboration panel a target name stands for while the container shoots it.</summary>
    public sealed class PanelContext {
        public string TargetName { get; init; } = "";
        public string ProjectId { get; init; } = "";
        public string ProjectName { get; init; } = "";
        public string TaskId { get; init; } = "";
        public int PanelNumber { get; init; }
        public string Filter { get; init; } = "";
        public Region Footprint { get; init; }
        public double? Scale { get; init; }
        public double? FocalLength { get; init; }
        public bool Colour { get; init; }
        public double? Bandpass { get; init; }
    }

    /// <summary>
    /// Credits every light N.I.N.A. saves to the panel it was shot over.
    ///
    /// The frame is matched to its panel by the target name N.I.N.A. stamps
    /// into the image's metadata, not by whatever the container happens to
    /// be doing when the save completes: saving runs behind capture, and the
    /// last frame of one panel is often written while the slew to the next
    /// is already under way.
    /// </summary>
    public sealed class FrameReporter : IDisposable {
        private readonly IImageSaveMediator images;
        private readonly FrameLedger ledger;
        private readonly ConcurrentDictionary<string, PanelContext> byTarget = new(StringComparer.Ordinal);
        private bool initialized;

        public FrameReporter(IImageSaveMediator images, FrameLedger ledger) {
            this.images = images;
            this.ledger = ledger;
        }

        public void Initialize() {
            if (initialized) return;
            images.ImageSaved += OnImageSaved;
            images.BeforeImageSaved += OnBeforeImageSaved;
            initialized = true;
        }

        public void Register(PanelContext context) => byTarget[context.TargetName] = context;

        public void Forget(string targetName) => byTarget.TryRemove(targetName, out _);

        public void ForgetAll() => byTarget.Clear();

        public PanelContext Lookup(string targetName) =>
            !string.IsNullOrEmpty(targetName) && byTarget.TryGetValue(targetName, out var context) ? context : null;

        private Task OnBeforeImageSaved(object sender, BeforeImageSavedEventArgs args) {
            try {
                var context = Lookup(args.Image?.MetaData?.Target?.Name);
                if (context != null && args.Image.MetaData.Image.ImageType == "LIGHT") {
                    args.Image.MetaData.GenericHeaders.Add(new StringMetaDataHeader("STARFRNT", context.ProjectId, "Starfront collaboration"));
                    args.Image.MetaData.GenericHeaders.Add(new IntMetaDataHeader("SFPANEL", context.PanelNumber, "Starfront panel number"));
                }
            } catch (Exception ex) {
                Logger.Debug($"Starfront: could not stamp the frame - {ex.Message}");
            }
            return Task.CompletedTask;
        }

        private void OnImageSaved(object sender, ImageSavedEventArgs args) {
            try {
                var metadata = args.MetaData;
                if (metadata?.Image == null || metadata.Image.ImageType != "LIGHT") return;
                var context = Lookup(metadata.Target?.Name);
                if (context == null) return;

                var filter = Filters.Canonical(string.IsNullOrWhiteSpace(args.Filter) ? context.Filter : args.Filter);
                if (string.IsNullOrEmpty(filter)) filter = context.Filter;
                var started = metadata.Image.ExposureStart != DateTime.MinValue ? metadata.Image.ExposureStart.ToLocalTime() : DateTime.Now;
                var night = SkyMath.NightName(started);
                var seconds = args.Duration > 0 ? args.Duration : metadata.Image.ExposureTime;
                if (double.IsNaN(seconds) || seconds <= 0) return;

                double? hfr = null;
                var hfrPixels = args.StarDetectionAnalysis?.HFR ?? double.NaN;
                if (!double.IsNaN(hfrPixels) && hfrPixels > 0 && context.Scale is double scale && scale > 0) hfr = hfrPixels * scale;

                double? rms = null;
                var recorded = metadata.Image.RecordedRMS;
                if (recorded != null && recorded.DataPoints > 0 && recorded.Total > 0) rms = recorded.Total * recorded.Scale;

                ledger.Credit(night, context.ProjectId, context.TaskId, context.PanelNumber.ToString(), filter, seconds,
                              hfr, rms, context.Footprint, context.Scale, context.FocalLength, context.Colour, context.Bandpass);
                var shown = string.IsNullOrWhiteSpace(args.Filter) ? filter : args.Filter.Trim();
                Logger.Info($"Starfront: {context.TargetName} {shown} +{seconds:0}s"
                            + (hfr.HasValue ? $", HFR {hfr:0.00}\"" : "") + (rms.HasValue ? $", guiding {rms:0.00}\"" : ""));
            } catch (Exception ex) {
                Logger.Error("Starfront: a saved frame could not be credited", ex);
            }
        }

        public void Dispose() {
            if (!initialized) return;
            images.ImageSaved -= OnImageSaved;
            images.BeforeImageSaved -= OnBeforeImageSaved;
            initialized = false;
            byTarget.Clear();
        }
    }
}
