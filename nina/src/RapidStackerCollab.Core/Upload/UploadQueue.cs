// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

using Microsoft.Data.Sqlite;
using RapidStackerCollab.Core.Api;
using RapidStackerCollab.Core.Fits;
using RapidStackerCollab.Core.Util;

namespace RapidStackerCollab.Core.Upload;

/// <summary>Item states in the persistent queue.</summary>
public static class QueueStates
{
    /// <summary>Waiting for its turn (or for <see cref="QueueItem.NextAttemptAt"/>).</summary>
    public const string Pending = "pending";
    /// <summary>Claimed by the runner. A row still in this state at start-up belonged to a run that died: see <see cref="UploadQueue.RecoverInterrupted"/>.</summary>
    public const string Uploading = "uploading";
    /// <summary>The server holds the file (<c>complete</c> succeeded). <see cref="QueueItem.Verdict"/> follows later.</summary>
    public const string Uploaded = "uploaded";
    /// <summary>The server already had it (<c>duplicate</c>).</summary>
    public const string Duplicate = "duplicate";
    /// <summary>Will not upload as it is; see <see cref="QueueItem.LastError"/>. <see cref="UploadQueue.Requeue"/> tries again.</summary>
    public const string Failed = "failed";
    /// <summary>Removed by the user. The row stays so the same file is not queued again by the next scan.</summary>
    public const string Cancelled = "cancelled";

    public static readonly IReadOnlyList<string> All = new[] { Pending, Uploading, Uploaded, Duplicate, Failed, Cancelled };
}

public sealed record NewQueueItem(
    string ProjectRef,
    string Path,
    string Filename,
    FrameKind Kind,
    InitHeader Header,
    string? RigId = null);

public sealed record QueueItem(
    long Id,
    string ProjectRef,
    string Path,
    string Filename,
    FrameKind Kind,
    InitHeader Header,
    string? RigId,
    long? Size,
    string? Sha256,
    string? FrameId,
    string State,
    int Attempts,
    string? LastError,
    string? LastErrorCode,
    DateTimeOffset NextAttemptAt,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt,
    DateTimeOffset? UploadedAt,
    string? Verdict,
    string? VerdictDetail);

public sealed record ProjectPause(string ProjectRef, string Reason, string? Code, DateTimeOffset PausedAt, DateTimeOffset? ResumeAt, bool ByUser);

/// <summary>
/// The upload queue, persisted in SQLite (<c>Microsoft.Data.Sqlite</c>) at an injected path
/// (<c>%LOCALAPPDATA%\NINA\RapidStackerCollab\queue.db</c> in the plugin) so it survives N.I.N.A. restarts.
/// A row keeps the hash and the server's <c>frame_id</c> once known, so a restart resumes instead of starting over.
/// One long-lived connection guarded by a lock; every operation is a single short statement or transaction.
/// </summary>
public sealed class UploadQueue : IDisposable
{
    private const int SchemaVersion = 1;
    private const string Columns =
        "id, project_ref, path, filename, frame_kind, header_json, rig_id, size, sha256, frame_id, state, attempts, last_error, " +
        "last_error_code, next_attempt_at, created_at, updated_at, uploaded_at, verdict, verdict_detail";

    private readonly object gate = new();
    private readonly SqliteConnection db;
    private readonly IClock clock;

    public UploadQueue(string databasePath, IClock clock)
    {
        this.clock = clock;
        var dir = System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(databasePath));
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        var cs = new SqliteConnectionStringBuilder { DataSource = databasePath, Mode = SqliteOpenMode.ReadWriteCreate, Pooling = false }.ToString();
        db = new SqliteConnection(cs);
        try
        {
            db.Open();
            Exec("PRAGMA journal_mode=WAL;");
            Exec("PRAGMA synchronous=NORMAL;");
            Exec("PRAGMA busy_timeout=5000;");
            Migrate();
        }
        catch
        {
            // A refused or broken database must not stay open: on Windows the handle locks queue.db (and its
            // -wal/-shm) until the finalizer runs, so the user could not move or delete it.
            db.Dispose();
            throw;
        }
    }

    public void Dispose()
    {
        lock (gate)
        {
            db.Dispose();
        }
        SqliteConnection.ClearAllPools();
    }

    // ---- writes --------------------------------------------------------------------------------------------------

    /// <summary>Queue a file. Idempotent per (project, path): an existing row, whatever its state, is returned unchanged with <c>Added = false</c>.</summary>
    public (QueueItem Item, bool Added) Enqueue(NewQueueItem item)
    {
        var now = Ms(clock.Now);
        lock (gate)
        {
            using var tx = db.BeginTransaction();
            var insert = Command("""
                INSERT INTO items (project_ref, path, filename, frame_kind, header_json, rig_id, state, attempts, next_attempt_at, created_at, updated_at)
                VALUES ($project, $path, $filename, $kind, $header, $rig, 'pending', 0, $now, $now, $now)
                ON CONFLICT(project_ref, path) DO NOTHING
                """, tx);
            insert.Parameters.AddWithValue("$project", item.ProjectRef);
            insert.Parameters.AddWithValue("$path", item.Path);
            insert.Parameters.AddWithValue("$filename", item.Filename);
            insert.Parameters.AddWithValue("$kind", item.Kind.ToString());
            insert.Parameters.AddWithValue("$header", ApiJson.Serialize(item.Header));
            insert.Parameters.AddWithValue("$rig", (object?)item.RigId ?? DBNull.Value);
            insert.Parameters.AddWithValue("$now", now);
            var added = insert.ExecuteNonQuery() == 1;
            var select = Command($"SELECT {Columns} FROM items WHERE project_ref = $project AND path = $path", tx);
            select.Parameters.AddWithValue("$project", item.ProjectRef);
            select.Parameters.AddWithValue("$path", item.Path);
            using var r = select.ExecuteReader();
            r.Read();
            var row = Read(r);
            r.Close();
            tx.Commit();
            return (row, added);
        }
    }

    /// <summary>
    /// Take the next item that may run now: pending, due, and not in a paused project, oldest first; marks it
    /// <see cref="QueueStates.Uploading"/> in the same statement so two callers can never get the same row.
    /// </summary>
    public QueueItem? ClaimNext()
    {
        var now = Ms(clock.Now);
        lock (gate)
        {
            var cmd = Command($"""
                UPDATE items SET state = 'uploading', updated_at = $now
                WHERE id = (
                    SELECT id FROM items
                    WHERE state = 'pending' AND next_attempt_at <= $now
                      AND project_ref NOT IN (SELECT project_ref FROM project_pauses WHERE resume_at IS NULL OR resume_at > $now)
                    ORDER BY created_at, id LIMIT 1)
                RETURNING {Columns}
                """);
            cmd.Parameters.AddWithValue("$now", now);
            using var r = cmd.ExecuteReader();
            return r.Read() ? Read(r) : null;
        }
    }

    /// <summary>Remember what is known mid-upload so a restart can resume: the hash, the size of the file sent, the server's frame id.</summary>
    public void RecordProgress(long id, string? sha256, long? size, string? frameId) => Update(id,
        "sha256 = COALESCE($sha, sha256), size = COALESCE($size, size), frame_id = COALESCE($frame, frame_id)",
        ("$sha", sha256), ("$size", size), ("$frame", frameId));

    public void MarkUploaded(long id, string? frameId, string? status) => Update(id,
        "state = 'uploaded', frame_id = COALESCE($frame, frame_id), verdict = $status, uploaded_at = $now, last_error = NULL, last_error_code = NULL",
        ("$frame", frameId), ("$status", status));

    public void MarkDuplicate(long id, string? frameId, string? status) => Update(id,
        "state = 'duplicate', frame_id = COALESCE($frame, frame_id), verdict = $status, uploaded_at = $now, last_error = NULL, last_error_code = NULL",
        ("$frame", frameId), ("$status", status));

    /// <summary>Back to pending for another go at <paramref name="nextAttemptAt"/>, counting one attempt.</summary>
    public void MarkRetry(long id, DateTimeOffset nextAttemptAt, string? code, string? message) => Update(id,
        "state = 'pending', attempts = attempts + 1, next_attempt_at = $next, last_error = $msg, last_error_code = $code",
        ("$next", Ms(nextAttemptAt)), ("$msg", message), ("$code", code));

    /// <summary>Back to pending without counting an attempt (the project is paused, or the policy deferred it).</summary>
    public void Release(long id) => Update(id, "state = 'pending'");

    public void MarkFailed(long id, string? code, string? message) => Update(id,
        "state = 'failed', attempts = attempts + 1, last_error = $msg, last_error_code = $code",
        ("$msg", message), ("$code", code));

    /// <summary>The user removed the item. Its row stays so a rescan does not queue the file again.</summary>
    public void Cancel(long id) => Update(id, "state = 'cancelled'");

    /// <summary>Failed or cancelled items go back to pending with a clean slate.</summary>
    public void Requeue(long id) => Update(id,
        "state = 'pending', attempts = 0, next_attempt_at = $now, last_error = NULL, last_error_code = NULL");

    /// <summary>Record the server's later verdict for an uploaded frame (<c>accepted</c>, <c>rejected</c> ...).</summary>
    public void SetVerdict(long id, string verdict, string? detail) => Update(id,
        "verdict = $verdict, verdict_detail = $detail", ("$verdict", verdict), ("$detail", detail));

    /// <summary>
    /// Start-up recovery: rows left <see cref="QueueStates.Uploading"/> by a run that died go back to pending, with their
    /// hash and frame id intact so the next run resumes them. Returns how many.
    /// </summary>
    public int RecoverInterrupted()
    {
        lock (gate)
        {
            var cmd = Command("UPDATE items SET state = 'pending', updated_at = $now WHERE state = 'uploading'");
            cmd.Parameters.AddWithValue("$now", Ms(clock.Now));
            return cmd.ExecuteNonQuery();
        }
    }

    /// <summary>Delete finished rows (uploaded, duplicate, cancelled) last touched before <paramref name="olderThan"/>. Pending and failed rows are never purged.</summary>
    public int Purge(DateTimeOffset olderThan)
    {
        lock (gate)
        {
            var cmd = Command("DELETE FROM items WHERE state IN ('uploaded','duplicate','cancelled') AND updated_at < $t");
            cmd.Parameters.AddWithValue("$t", Ms(olderThan));
            return cmd.ExecuteNonQuery();
        }
    }

    // ---- project pauses ------------------------------------------------------------------------------------------

    /// <summary>Stop claiming items of one project (quota reached, not open, or the user's own pause). <paramref name="resumeAt"/> null means until <see cref="ResumeProject"/>.</summary>
    public void PauseProject(string projectRef, string reason, string? code = null, DateTimeOffset? resumeAt = null, bool byUser = false)
    {
        lock (gate)
        {
            var cmd = Command("""
                INSERT INTO project_pauses (project_ref, reason, code, paused_at, resume_at, by_user)
                VALUES ($project, $reason, $code, $now, $resume, $user)
                ON CONFLICT(project_ref) DO UPDATE SET reason = $reason, code = $code, resume_at = $resume, by_user = $user
                """);
            cmd.Parameters.AddWithValue("$project", projectRef);
            cmd.Parameters.AddWithValue("$reason", reason);
            cmd.Parameters.AddWithValue("$code", (object?)code ?? DBNull.Value);
            cmd.Parameters.AddWithValue("$now", Ms(clock.Now));
            cmd.Parameters.AddWithValue("$resume", resumeAt is { } r ? Ms(r) : DBNull.Value);
            cmd.Parameters.AddWithValue("$user", byUser ? 1 : 0);
            cmd.ExecuteNonQuery();
        }
    }

    public void ResumeProject(string projectRef)
    {
        lock (gate)
        {
            var cmd = Command("DELETE FROM project_pauses WHERE project_ref = $project");
            cmd.Parameters.AddWithValue("$project", projectRef);
            cmd.ExecuteNonQuery();
        }
    }

    /// <summary>Pauses in force now (those with a <c>resume_at</c> in the past are no longer in force).</summary>
    public IReadOnlyList<ProjectPause> ActivePauses()
    {
        lock (gate)
        {
            var cmd = Command("SELECT project_ref, reason, code, paused_at, resume_at, by_user FROM project_pauses WHERE resume_at IS NULL OR resume_at > $now ORDER BY paused_at");
            cmd.Parameters.AddWithValue("$now", Ms(clock.Now));
            using var r = cmd.ExecuteReader();
            var list = new List<ProjectPause>();
            while (r.Read())
                list.Add(new ProjectPause(r.GetString(0), r.GetString(1), r.IsDBNull(2) ? null : r.GetString(2), FromMs(r.GetInt64(3)),
                    r.IsDBNull(4) ? null : FromMs(r.GetInt64(4)), r.GetInt64(5) != 0));
            return list;
        }
    }

    public bool IsPaused(string projectRef) => ActivePauses().Any(p => p.ProjectRef == projectRef);

    // ---- reads ---------------------------------------------------------------------------------------------------

    public QueueItem? Get(long id)
    {
        lock (gate)
        {
            var cmd = Command($"SELECT {Columns} FROM items WHERE id = $id");
            cmd.Parameters.AddWithValue("$id", id);
            using var r = cmd.ExecuteReader();
            return r.Read() ? Read(r) : null;
        }
    }

    /// <summary>The row for (project, path), or null when this file was never queued for that project.</summary>
    public QueueItem? Find(string projectRef, string path)
    {
        lock (gate)
        {
            var cmd = Command($"SELECT {Columns} FROM items WHERE project_ref = $project AND path = $path");
            cmd.Parameters.AddWithValue("$project", projectRef);
            cmd.Parameters.AddWithValue("$path", path);
            using var r = cmd.ExecuteReader();
            return r.Read() ? Read(r) : null;
        }
    }

    public IReadOnlyList<QueueItem> List(string? state = null, string? projectRef = null, int limit = 500)
    {
        lock (gate)
        {
            var cmd = Command($"""
                SELECT {Columns} FROM items
                WHERE ($state IS NULL OR state = $state) AND ($project IS NULL OR project_ref = $project)
                ORDER BY created_at, id LIMIT $limit
                """);
            cmd.Parameters.AddWithValue("$state", (object?)state ?? DBNull.Value);
            cmd.Parameters.AddWithValue("$project", (object?)projectRef ?? DBNull.Value);
            cmd.Parameters.AddWithValue("$limit", limit);
            using var r = cmd.ExecuteReader();
            var list = new List<QueueItem>();
            while (r.Read()) list.Add(Read(r));
            return list;
        }
    }

    /// <summary>What is still to do: the item being uploaded first, then pending ones in the order they will be tried.</summary>
    public IReadOnlyList<QueueItem> Active(int limit = 100)
    {
        lock (gate)
        {
            var cmd = Command($"""
                SELECT {Columns} FROM items WHERE state IN ('uploading','pending')
                ORDER BY CASE state WHEN 'uploading' THEN 0 ELSE 1 END, next_attempt_at, id LIMIT $limit
                """);
            cmd.Parameters.AddWithValue("$limit", limit);
            using var r = cmd.ExecuteReader();
            var list = new List<QueueItem>();
            while (r.Read()) list.Add(Read(r));
            return list;
        }
    }

    /// <summary>The most recently finished items (uploaded, duplicate or failed), newest first: the dock panel's verdict list.</summary>
    public IReadOnlyList<QueueItem> Finished(int limit = 20)
    {
        lock (gate)
        {
            var cmd = Command($"""
                SELECT {Columns} FROM items WHERE state IN ('uploaded','duplicate','failed')
                ORDER BY updated_at DESC, id DESC LIMIT $limit
                """);
            cmd.Parameters.AddWithValue("$limit", limit);
            using var r = cmd.ExecuteReader();
            var list = new List<QueueItem>();
            while (r.Read()) list.Add(Read(r));
            return list;
        }
    }

    public IReadOnlyDictionary<string, int> CountsByState()
    {
        lock (gate)
        {
            var cmd = Command("SELECT state, COUNT(*) FROM items GROUP BY state");
            using var r = cmd.ExecuteReader();
            var d = QueueStates.All.ToDictionary(s => s, _ => 0);
            while (r.Read()) d[r.GetString(0)] = r.GetInt32(1);
            return d;
        }
    }

    /// <summary>Uploaded items whose final verdict has not been read from the server yet (for the dock panel's polling).</summary>
    public IReadOnlyList<QueueItem> AwaitingVerdict(int limit = 100)
    {
        lock (gate)
        {
            var cmd = Command($"""
                SELECT {Columns} FROM items
                WHERE state IN ('uploaded','duplicate') AND frame_id IS NOT NULL
                  AND (verdict IS NULL OR verdict IN ('uploading','uploaded','preparing'))
                ORDER BY uploaded_at LIMIT $limit
                """);
            cmd.Parameters.AddWithValue("$limit", limit);
            using var r = cmd.ExecuteReader();
            var list = new List<QueueItem>();
            while (r.Read()) list.Add(Read(r));
            return list;
        }
    }

    /// <summary>When the next pending item (outside paused projects) becomes due, or null when none is pending.</summary>
    public DateTimeOffset? NextDueAt()
    {
        lock (gate)
        {
            var cmd = Command("""
                SELECT MIN(MAX(next_attempt_at, COALESCE((SELECT resume_at FROM project_pauses p WHERE p.project_ref = items.project_ref), 0)))
                FROM items WHERE state = 'pending'
                  AND project_ref NOT IN (SELECT project_ref FROM project_pauses WHERE resume_at IS NULL)
                """);
            var v = cmd.ExecuteScalar();
            return v is long ms ? FromMs(ms) : null;
        }
    }

    // ---- plumbing ------------------------------------------------------------------------------------------------

    private void Migrate()
    {
        lock (gate)
        {
            Exec("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);");
            var v = Scalar("SELECT value FROM meta WHERE key = 'schema_version'");
            var current = v is string s ? int.Parse(s) : 0;
            if (current > SchemaVersion)
                throw new InvalidOperationException($"The upload queue database is from a newer plugin (schema {current}).");
            if (current < 1)
            {
                Exec("""
                    CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_ref TEXT NOT NULL,
                        path TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        frame_kind TEXT NOT NULL,
                        header_json TEXT NOT NULL,
                        rig_id TEXT NULL,
                        size INTEGER NULL,
                        sha256 TEXT NULL,
                        frame_id TEXT NULL,
                        state TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NULL,
                        last_error_code TEXT NULL,
                        next_attempt_at INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        uploaded_at INTEGER NULL,
                        verdict TEXT NULL,
                        verdict_detail TEXT NULL,
                        UNIQUE (project_ref, path)
                    );
                    CREATE INDEX IF NOT EXISTS items_due ON items (state, next_attempt_at);
                    CREATE TABLE IF NOT EXISTS project_pauses (
                        project_ref TEXT PRIMARY KEY,
                        reason TEXT NOT NULL,
                        code TEXT NULL,
                        paused_at INTEGER NOT NULL,
                        resume_at INTEGER NULL,
                        by_user INTEGER NOT NULL DEFAULT 0
                    );
                    """);
                Exec($"INSERT INTO meta (key, value) VALUES ('schema_version', '{SchemaVersion}') ON CONFLICT(key) DO UPDATE SET value = excluded.value;");
            }
        }
    }

    private void Update(long id, string set, params (string Name, object? Value)[] parameters)
    {
        lock (gate)
        {
            var cmd = Command($"UPDATE items SET {set}, updated_at = $now WHERE id = $id");
            cmd.Parameters.AddWithValue("$id", id);
            cmd.Parameters.AddWithValue("$now", Ms(clock.Now));
            foreach (var (n, v) in parameters) cmd.Parameters.AddWithValue(n, v ?? DBNull.Value);
            cmd.ExecuteNonQuery();
        }
    }

    private SqliteCommand Command(string sql, SqliteTransaction? tx = null)
    {
        var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.Transaction = tx;
        return cmd;
    }

    private void Exec(string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    private object? Scalar(string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        return cmd.ExecuteScalar();
    }

    private static long Ms(DateTimeOffset t) => t.ToUnixTimeMilliseconds();

    private static DateTimeOffset FromMs(long ms) => DateTimeOffset.FromUnixTimeMilliseconds(ms);

    private static QueueItem Read(SqliteDataReader r)
    {
        string? S(int i) => r.IsDBNull(i) ? null : r.GetString(i);
        long? L(int i) => r.IsDBNull(i) ? null : r.GetInt64(i);
        return new QueueItem(
            Id: r.GetInt64(0),
            ProjectRef: r.GetString(1),
            Path: r.GetString(2),
            Filename: r.GetString(3),
            Kind: Enum.TryParse<FrameKind>(r.GetString(4), out var k) ? k : FrameKind.Other,
            Header: ApiJson.Deserialize<InitHeader>(r.GetString(5)) ?? new InitHeader(),
            RigId: S(6),
            Size: L(7),
            Sha256: S(8),
            FrameId: S(9),
            State: r.GetString(10),
            Attempts: r.GetInt32(11),
            LastError: S(12),
            LastErrorCode: S(13),
            NextAttemptAt: FromMs(r.GetInt64(14)),
            CreatedAt: FromMs(r.GetInt64(15)),
            UpdatedAt: FromMs(r.GetInt64(16)),
            UploadedAt: L(17) is { } u ? FromMs(u) : null,
            Verdict: S(18),
            VerdictDetail: S(19));
    }
}
