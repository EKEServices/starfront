using System.IO;
using Newtonsoft.Json;
using NINA.Core.Utility;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>What a link to a stacking project is in, as the options page and the log say it.</summary>
    public static class LinkStatus {
        /// <summary>Joined; frames go.</summary>
        public const string Active = "active";
        /// <summary>Joined, waiting for the project's owner to approve; frames queue and wait.</summary>
        public const string Pending = "pending";
        /// <summary>The project's terms changed; somebody has to read and accept them before frames go.</summary>
        public const string TermsOutdated = "terms_outdated";
        /// <summary>The camera has not told the plugin what it is yet.</summary>
        public const string NeedsRig = "needs_rig";
        /// <summary>None of this wheel's filters is one the project wants.</summary>
        public const string NoFilters = "no_filters";
        /// <summary>The join did not go through; the message says why and when it is tried again.</summary>
        public const string Failed = "failed";
    }

    /// <summary>One Starfront collaboration's link to the Rapid Stacker Collab project that stacks its lights.</summary>
    public sealed class StackingLink {
        public string StarfrontProjectId { get; set; } = "";
        public string StarfrontName { get; set; } = "";
        public string JoinCode { get; set; } = "";
        public string CollabProjectId { get; set; } = "";
        public string CollabName { get; set; } = "";
        public string Status { get; set; } = LinkStatus.Failed;
        public string Message { get; set; } = "";
        public string TermsVersion { get; set; } = "";
        public string TermsUrl { get; set; } = "";
        public string RigId { get; set; } = "";
        public string DisplayName { get; set; } = "";
        /// <summary>The project insists on frames with the observer's location removed.</summary>
        public bool RequireStrip { get; set; }
        public List<string> DeclaredFilters { get; set; } = new();
        /// <summary>The wheel's names and the project's filter each delivers, or SKIP; what the membership holds.</summary>
        public Dictionary<string, string> Mapping { get; set; } = new();
        public DateTime UpdatedUtc { get; set; }
        /// <summary>Not before this is a join tried again; null means at the next check-in.</summary>
        public DateTime? NextAttemptUtc { get; set; }

        [JsonIgnore] public bool Active => Status == LinkStatus.Active && CollabProjectId.Length > 0;
        [JsonIgnore] public bool Feeds => CollabProjectId.Length > 0 && Status is LinkStatus.Active or LinkStatus.Pending;

        /// <summary>Whether a light through this wheel filter is one the project never wants.</summary>
        public bool Skips(string rawFilter) {
            if (string.IsNullOrWhiteSpace(rawFilter)) return false;
            foreach (var kv in Mapping) {
                if (string.Equals(kv.Key.Trim(), rawFilter.Trim(), StringComparison.OrdinalIgnoreCase)) return kv.Value == "SKIP";
            }
            return false;
        }

        public string Describe() => Status switch {
            LinkStatus.Active => $"Joined {CollabName}",
            LinkStatus.Pending => $"Joined {CollabName}; waiting for its owner to approve",
            LinkStatus.TermsOutdated => $"The terms of {CollabName} changed; accept them to carry on",
            LinkStatus.NeedsRig => "Not joined yet: " + Message,
            LinkStatus.NoFilters => $"Not joined: none of this wheel's filters is one {CollabName} wants",
            _ => "Not joined: " + Message,
        };
    }

    /// <summary>
    /// The links on disk, one per Starfront collaboration, in the plugin's
    /// own folder beside the ledger and with its habits: written whole to a
    /// temporary file and moved into place, and a file that cannot be read
    /// is kept aside rather than overwritten.
    /// </summary>
    public sealed class StackingLinks {
        private readonly object gate = new();
        private readonly string path;
        private Dictionary<string, StackingLink> links = new(StringComparer.Ordinal);

        public StackingLinks(string path) => this.path = path;

        public event EventHandler Changed;

        public void Load() {
            lock (gate) {
                try {
                    if (File.Exists(path)) {
                        var rows = JsonConvert.DeserializeObject<List<StackingLink>>(File.ReadAllText(path)) ?? new List<StackingLink>();
                        links = rows.Where(l => !string.IsNullOrEmpty(l.StarfrontProjectId)).ToDictionary(l => l.StarfrontProjectId, l => l, StringComparer.Ordinal);
                    }
                } catch (Exception ex) {
                    Logger.Error("Starfront stacking: the links file could not be read; starting afresh", ex);
                    try { File.Copy(path, path + ".broken", true); } catch (Exception) { }
                    links = new Dictionary<string, StackingLink>(StringComparer.Ordinal);
                }
            }
            try { Changed?.Invoke(this, EventArgs.Empty); } catch (Exception ex) { Logger.Error("Starfront stacking: a links handler failed", ex); }
        }

        private void Save() {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var temporary = path + ".tmp";
            var rows = links.Values.OrderBy(l => l.StarfrontName, StringComparer.OrdinalIgnoreCase).ToList();
            File.WriteAllText(temporary, JsonConvert.SerializeObject(rows, Formatting.Indented));
            File.Move(temporary, path, true);
        }

        public StackingLink Lookup(string starfrontProjectId) {
            lock (gate) return !string.IsNullOrEmpty(starfrontProjectId) && links.TryGetValue(starfrontProjectId, out var link) ? link : null;
        }

        public StackingLink ByCollabProject(string collabProjectId) {
            lock (gate) return links.Values.FirstOrDefault(l => l.CollabProjectId == collabProjectId);
        }

        public IReadOnlyList<StackingLink> All() {
            lock (gate) return links.Values.OrderBy(l => l.StarfrontName, StringComparer.OrdinalIgnoreCase).ToList();
        }

        /// <summary>The project insists on stripped frames: the runner strips whatever the person's own switch says.</summary>
        public bool RequireStrip(string collabProjectId) => ByCollabProject(collabProjectId)?.RequireStrip ?? false;

        public void Upsert(StackingLink link) {
            lock (gate) {
                link.UpdatedUtc = DateTime.UtcNow;
                links[link.StarfrontProjectId] = link;
                try { Save(); } catch (Exception ex) { Logger.Error("Starfront stacking: the links file could not be written", ex); }
            }
            try { Changed?.Invoke(this, EventArgs.Empty); } catch (Exception ex) { Logger.Error("Starfront stacking: a links handler failed", ex); }
        }

        public void Remove(string starfrontProjectId) {
            lock (gate) {
                if (!links.Remove(starfrontProjectId)) return;
                try { Save(); } catch (Exception ex) { Logger.Error("Starfront stacking: the links file could not be written", ex); }
            }
            try { Changed?.Invoke(this, EventArgs.Empty); } catch (Exception ex) { Logger.Error("Starfront stacking: a links handler failed", ex); }
        }
    }
}
