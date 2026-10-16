// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

namespace RapidStackerCollab.Core.Api;

/// <summary>API-token scopes (openapi.yaml <c>Scope</c>). The device flow grants the first five.</summary>
public static class ApiScopes
{
    public const string ProjectsRead = "projects:read";
    public const string ProjectsJoin = "projects:join";
    public const string FramesRead = "frames:read";
    public const string FramesWrite = "frames:write";
    public const string MastersRead = "masters:read";
    public const string ProjectsManage = "projects:manage";

    /// <summary>What the plugin asks for. <c>projects:manage</c> is never requested: the device flow refuses it.</summary>
    public static readonly IReadOnlyList<string> PluginDefaults =
        new[] { ProjectsRead, ProjectsJoin, FramesRead, FramesWrite, MastersRead };
}

/// <summary>Frame statuses (<c>FrameStatus</c>). Strings, so an unknown future status survives a round trip.</summary>
public static class FrameStatuses
{
    public const string Uploading = "uploading";
    public const string Uploaded = "uploaded";
    public const string Preparing = "preparing";
    public const string Accepted = "accepted";
    public const string Rejected = "rejected";
    public const string Quarantined = "quarantined";
    public const string Accumulated = "accumulated";
    public const string InGold = "in_gold";
    public const string Failed = "failed";
    public const string Withdrawn = "withdrawn";

    /// <summary>Statuses that mean the server accepted the frame into a stack.</summary>
    public static bool IsAcceptedFamily(string? s) => s is Accepted or Accumulated or InGold;

    /// <summary>Statuses that are a final verdict the dock panel stops polling for.</summary>
    public static bool IsFinalVerdict(string? s) =>
        s is Accepted or Accumulated or InGold or Rejected or Quarantined or Failed or Withdrawn;
}

/// <summary>Project statuses (<c>ProjectStatus</c>).</summary>
public static class ProjectStatuses
{
    public const string Draft = "draft";
    public const string Open = "open";
    public const string Paused = "paused";
    public const string Closed = "closed";
    public const string Archived = "archived";
    /// <summary>Scheduled for deletion (contract 1.16.0); <c>delete_after</c> says when the purge may start.</summary>
    public const string Deleted = "deleted";
}

/// <summary>Member statuses (<c>MemberStatus</c>).</summary>
public static class MemberStatuses
{
    public const string Active = "active";
    public const string Pending = "pending";
    public const string Removed = "removed";
}

/// <summary>Quota names carried by 429 <c>quota_exceeded</c>.</summary>
public static class QuotaNames
{
    public const string ContributorBytes = "contributor_bytes";
    public const string ContributorFrames = "contributor_frames";
    public const string ProjectBytes = "project_bytes";
}

/// <summary>Values of <c>SKIP</c> in a contributor mapping: never upload frames with this wheel name.</summary>
public static class MappingValues
{
    public const string Skip = "SKIP";
}
