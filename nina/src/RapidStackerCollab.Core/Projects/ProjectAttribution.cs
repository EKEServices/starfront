// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

using System.Text;
using RapidStackerCollab.Core.Api;

namespace RapidStackerCollab.Core.Projects;

/// <summary>A project the user has joined, with what attribution needs to know about it.</summary>
public sealed record JoinedProject(string ProjectId, string Slug, string Name, string TargetName, Grid? Grid, DateTimeOffset JoinedAt);

/// <summary>What a saved frame says about where it was pointed and at what.</summary>
public sealed record FramePointing(string? ObjectName, double? RaDeg, double? DecDeg)
{
    public bool HasPosition => RaDeg.HasValue && DecDeg.HasValue;

    /// <summary>
    /// From a header: <c>RA</c>/<c>DEC</c> (degrees, or sexagesimal where RA is hours) first, then
    /// <c>OBJCTRA</c>/<c>OBJCTDEC</c> (hours and degrees, the FITS convention).
    /// </summary>
    public static FramePointing FromHeader(InitHeader h)
    {
        double? ra = null, dec = null;
        if (SkyMath.TryParseRa(Unbox(h.Ra), plainNumberIsHours: false, out var r) && SkyMath.TryParseDec(Unbox(h.Dec), out var d))
        {
            ra = r;
            dec = d;
        }
        else if (SkyMath.TryParseRa(h.ObjctRa, plainNumberIsHours: true, out r) && SkyMath.TryParseDec(h.ObjctDec, out d))
        {
            ra = r;
            dec = d;
        }
        return new FramePointing(h.Object, ra, dec);
    }

    // A header read back from the queue database carries RA/DEC as JsonElement.
    private static object? Unbox(object? v) => v is System.Text.Json.JsonElement e
        ? e.ValueKind switch
        {
            System.Text.Json.JsonValueKind.Number => e.GetDouble(),
            System.Text.Json.JsonValueKind.String => e.GetString(),
            _ => null,
        }
        : v;
}

public enum AttributionMode
{
    /// <summary>Every frame goes to the active project, whatever it shows.</summary>
    ActiveOnly,
    /// <summary>Match by target name or grid footprint; frames that match nothing are not attributed.</summary>
    Auto,
    /// <summary>As <see cref="Auto"/>, but a frame that matches nothing goes to the active project.</summary>
    AutoWithActiveFallback,
}

public enum AttributionReason
{
    None,
    ActiveProject,
    ObjectName,
    GridFootprint,
    ObjectAndFootprint,
}

/// <param name="Project">The chosen project, or null when there is none or the choice is ambiguous.</param>
/// <param name="Candidates">Every project that matched, best first. More than one with <see cref="Ambiguous"/> means the user should choose.</param>
public sealed record AttributionResult(JoinedProject? Project, AttributionReason Reason, IReadOnlyList<JoinedProject> Candidates, bool Ambiguous);

/// <summary>
/// Which joined project a saved frame belongs to (plan §8.1): its <c>OBJECT</c> matches the project's target, or its
/// RA/Dec falls inside the project's grid footprint. A frame that satisfies both ranks above one that satisfies only the
/// footprint, which ranks above one that satisfies only the name (names are free text; positions are not). When several
/// projects tie, the active project wins if it is one of them; otherwise the result is ambiguous and unattributed.
/// Calibration frames carry no pointing; the caller sends them to the active project.
/// </summary>
public static class ProjectAttribution
{
    public static AttributionResult Attribute(FramePointing frame, IReadOnlyList<JoinedProject> projects, string? activeProjectId = null,
        AttributionMode mode = AttributionMode.Auto, double footprintMarginPx = 0)
    {
        var active = activeProjectId is null ? null : projects.FirstOrDefault(p => p.ProjectId == activeProjectId);
        if (mode == AttributionMode.ActiveOnly)
            return active is null
                ? new AttributionResult(null, AttributionReason.None, Array.Empty<JoinedProject>(), false)
                : new AttributionResult(active, AttributionReason.ActiveProject, new[] { active }, false);

        var scored = new List<(JoinedProject P, int Score, AttributionReason Reason)>();
        foreach (var p in projects)
        {
            var byName = NameMatches(frame.ObjectName, p);
            var byFootprint = frame.HasPosition && p.Grid is { } g && SkyMath.InsideGrid(g, frame.RaDeg!.Value, frame.DecDeg!.Value, footprintMarginPx);
            if (byName && byFootprint) scored.Add((p, 3, AttributionReason.ObjectAndFootprint));
            else if (byFootprint) scored.Add((p, 2, AttributionReason.GridFootprint));
            else if (byName) scored.Add((p, 1, AttributionReason.ObjectName));
        }

        if (scored.Count == 0)
        {
            if (mode == AttributionMode.AutoWithActiveFallback && active is not null)
                return new AttributionResult(active, AttributionReason.ActiveProject, new[] { active }, false);
            return new AttributionResult(null, AttributionReason.None, Array.Empty<JoinedProject>(), false);
        }

        var ordered = scored.OrderByDescending(s => s.Score).ThenByDescending(s => s.P.JoinedAt).ToList();
        var top = ordered[0].Score;
        var best = ordered.Where(s => s.Score == top).ToList();
        var candidates = ordered.Select(s => s.P).ToList();
        if (best.Count == 1)
            return new AttributionResult(best[0].P, best[0].Reason, candidates, false);
        var activeBest = best.FirstOrDefault(s => s.P.ProjectId == activeProjectId);
        if (activeBest.P is not null)
            return new AttributionResult(activeBest.P, activeBest.Reason, candidates, false);
        return new AttributionResult(null, best[0].Reason, candidates, true);
    }

    /// <summary>
    /// <c>OBJECT</c> against the project's target and name: equal after lower-casing and dropping everything that is not a
    /// letter or digit ("NGC 7000" = "ngc-7000"), also against each part of "M42 - Orion Nebula" split at dashes, slashes,
    /// commas, semicolons and brackets.
    /// </summary>
    public static bool NameMatches(string? objectName, JoinedProject project)
    {
        var o = Key(objectName);
        if (o.Length == 0) return false;
        foreach (var source in new[] { project.TargetName, project.Name })
        {
            if (Key(source) == o) return true;
            foreach (var part in source.Split(new[] { " - ", " – ", " — ", "/", ",", ";", "(", ")", "[", "]", "|" }, StringSplitOptions.RemoveEmptyEntries))
                if (Key(part) == o) return true;
        }
        return false;
    }

    private static string Key(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return "";
        var sb = new StringBuilder(s.Length);
        foreach (var c in s.Normalize(NormalizationForm.FormKC))
            if (char.IsLetterOrDigit(c)) sb.Append(char.ToLowerInvariant(c));
        return sb.ToString();
    }
}
