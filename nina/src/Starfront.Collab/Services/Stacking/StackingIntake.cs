using System.IO;
using System.Text;
using NINA.Core.Utility;
using RapidStackerCollab.Core.Api;
using RapidStackerCollab.Core.Fits;
using RapidStackerCollab.Core.Upload;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>What becomes of a saved file: queued for its link, or skipped for the reason given.</summary>
    public sealed record IntakeDecision(bool Enqueue, string Reason, FrameKind Kind, InitHeader Header);

    /// <summary>
    /// From a file on disk to a row in the upload queue: read the header,
    /// tell a light from a calibration frame from a snapshot, and queue what
    /// the link wants. Starfront already knows which collaboration a light
    /// belongs to, so there is no guessing from the target name or the
    /// pointing here. The file is opened read-only and never written; the
    /// only thing written is the plugin's own queue.
    /// </summary>
    public sealed class StackingIntake {
        private readonly UploadQueue queue;
        private readonly StarfrontSettings settings;

        public StackingIntake(UploadQueue queue, StarfrontSettings settings) {
            this.queue = queue;
            this.settings = settings;
        }

        /// <summary>Raised when a file was queued, so the runner is woken.</summary>
        public event Action<QueueItem> Enqueued;

        /// <summary>Never throws.</summary>
        public IntakeDecision Evaluate(string path, string filterHint, StackingLink link, bool includeCalibration) {
            InitHeader header;
            try {
                if (!File.Exists(path)) return Skip("the file is not there");
                var format = Sniff(path);
                if (format == Format.Other) return Skip("not a FITS or XISF file");
                header = format == Format.Xisf ? XisfHeaderReader.Read(path) : FitsHeaderReader.Read(path);
            } catch (Exception ex) when (ex is FitsFormatException or IOException or UnauthorizedAccessException) {
                return Skip("the header could not be read: " + ex.Message);
            } catch (Exception ex) {
                return Skip("the file could not be read: " + ex.Message);
            }

            if (string.IsNullOrWhiteSpace(header.Filter) && !string.IsNullOrWhiteSpace(filterHint)) header.Filter = filterHint.Trim();

            var kind = FrameKinds.Classify(header.ImageTyp);
            if (kind == FrameKind.Other) return Skip($"not a light or a calibration frame (IMAGETYP '{header.ImageTyp ?? "missing"}')", kind, header);
            if (!FrameKinds.ShouldUpload(kind, includeCalibration)) return Skip("calibration frames are not sent", kind, header);
            if (link == null || !link.Feeds) return Skip("the collaboration is not linked to a stacking project", kind, header);
            if (kind == FrameKind.Light && link.Skips(header.Filter)) return Skip($"filter '{header.Filter}' is one {link.CollabName} does not want", kind, header);
            return new IntakeDecision(true, link.Status == LinkStatus.Pending ? $"queued for {link.CollabName} (waiting for its owner's approval)" : $"queued for {link.CollabName}", kind, header);
        }

        /// <summary>Queue a decision that says so. Idempotent per project and path.</summary>
        public (QueueItem Item, bool Added)? Enqueue(string path, IntakeDecision decision, StackingLink link) {
            if (!decision.Enqueue || decision.Header == null || link == null) return null;
            var rig = settings.StackingRigId;
            var result = queue.Enqueue(new NewQueueItem(link.CollabProjectId, path, Path.GetFileName(path), decision.Kind, decision.Header,
                                                        rig.Length > 0 ? rig : null));
            if (result.Added) {
                try { Enqueued?.Invoke(result.Item); } catch (Exception ex) { Logger.Error("Starfront stacking: an enqueue handler failed", ex); }
            }
            return result;
        }

        private static IntakeDecision Skip(string reason, FrameKind kind = FrameKind.Other, InitHeader header = null) => new(false, reason, kind, header);

        private enum Format { Fits, Xisf, Other }

        private static Format Sniff(string path) {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            var bytes = new byte[8];
            var n = 0;
            while (n < bytes.Length) {
                var got = stream.Read(bytes, n, bytes.Length - n);
                if (got <= 0) break;
                n += got;
            }
            var text = Encoding.ASCII.GetString(bytes, 0, n);
            if (text.StartsWith("XISF0100", StringComparison.Ordinal)) return Format.Xisf;
            if (text.StartsWith("SIMPLE", StringComparison.Ordinal)) return Format.Fits;
            return Format.Other;
        }
    }
}
