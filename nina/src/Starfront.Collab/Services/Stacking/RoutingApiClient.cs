using RapidStackerCollab.Core.Api;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>What the router needs to know about the world: the links, the default backend and the clients.</summary>
    public interface IStackingRoutes {
        /// <summary>The link whose stacking project this is (the id the engine and the queue carry), or null.</summary>
        StackingLink LinkOfProject(string project);
        /// <summary>"cloud" or "hub": where rig- and library-scoped calls go (decision D001).</summary>
        string DefaultBackend { get; }
        /// <summary>The Rapid Stacker Collab cloud client (the <c>rscu_</c> token).</summary>
        IApiClient Cloud { get; }
        /// <summary>The client a link talks through (its effective base and its token source).</summary>
        IApiClient ForLink(StackingLink link);
        /// <summary>The link whose hub carries rig-scoped calls when the default backend is the hub, or null when no hub link is usable.</summary>
        StackingLink DefaultHubLink();
    }

    /// <summary>
    /// The one <see cref="IApiClient"/> the upload engine, the verdict poller, the joiner and the library
    /// sync hold, so Core stays verbatim while a night's frames go to more than one backend. Project-scoped
    /// calls go to the client of the link whose <c>CollabProjectId</c> is the argument; a project nobody
    /// linked goes to the cloud client, which is what every call did before hubs. Rig- and library-scoped
    /// calls go to the default backend (the first usable hub link's client when that is the hub, and the
    /// cloud when no hub link is usable). Device sign-in, revoking the token, the project lists and join-code
    /// resolution are the cloud's, and the panels file is a plain GET that carries no token.
    /// </summary>
    public sealed class RoutingApiClient : IApiClient {
        private readonly IStackingRoutes routes;

        public RoutingApiClient(IStackingRoutes routes) => this.routes = routes;

        private IApiClient Cloud => routes.Cloud;

        private IApiClient ForProject(string project) {
            var link = string.IsNullOrEmpty(project) ? null : routes.LinkOfProject(project);
            return link == null ? Cloud : routes.ForLink(link);
        }

        private IApiClient ForDefault() {
            if (string.Equals(routes.DefaultBackend, "hub", StringComparison.OrdinalIgnoreCase)) {
                var hub = routes.DefaultHubLink();
                if (hub != null) return routes.ForLink(hub);
            }
            return Cloud;
        }

        // Device sign-in and the cloud token.
        public Task<DeviceStartResponse> DeviceStartAsync(DeviceStartRequest request, CancellationToken ct = default) => Cloud.DeviceStartAsync(request, ct);
        public Task<DevicePollResponse> DevicePollAsync(string deviceCode, CancellationToken ct = default) => Cloud.DevicePollAsync(deviceCode, ct);
        public Task RevokeOwnTokenAsync(CancellationToken ct = default) => Cloud.RevokeOwnTokenAsync(ct);

        // The cloud's directory.
        public Task<ProjectListResponse> ListProjectsAsync(string scope = "mine", int? limit = null, string cursor = null, CancellationToken ct = default) => Cloud.ListProjectsAsync(scope, limit, cursor, ct);
        public Task<ResolveJoinCodeResponse> ResolveJoinCodeAsync(string code, CancellationToken ct = default) => Cloud.ResolveJoinCodeAsync(code, ct);
        public Task<PanelsDocument> GetPanelsDocumentAsync(string url, CancellationToken ct = default) => Cloud.GetPanelsDocumentAsync(url, ct);

        // Rig- and library-scoped: the default backend.
        public Task<IReadOnlyList<Rig>> ListRigsAsync(CancellationToken ct = default) => ForDefault().ListRigsAsync(ct);
        public Task<Rig> UpsertRigAsync(RigUpsertRequest request, CancellationToken ct = default) => ForDefault().UpsertRigAsync(request, ct);
        public Task<Rig> PatchRigAsync(string rigId, RigPatchRequest request, CancellationToken ct = default) => ForDefault().PatchRigAsync(rigId, request, ct);
        public Task<LibraryResponse> GetLibraryAsync(string rigId, int? limit = null, string cursor = null, CancellationToken ct = default) => ForDefault().GetLibraryAsync(rigId, limit, cursor, ct);
        public Task<LibraryFrameInitResponse> InitLibraryFrameAsync(string rigId, LibraryFrameInitRequest request, CancellationToken ct = default) => ForDefault().InitLibraryFrameAsync(rigId, request, ct);
        public Task<LibraryFrameCompleteResponse> CompleteLibraryFrameAsync(string rigId, string frameId, IReadOnlyList<CompletedPart> etags, CancellationToken ct = default) => ForDefault().CompleteLibraryFrameAsync(rigId, frameId, etags, ct);
        public Task<WithdrawResponse> WithdrawLibraryFrameAsync(string rigId, string frameId, CancellationToken ct = default) => ForDefault().WithdrawLibraryFrameAsync(rigId, frameId, ct);

        /// <summary>Names are normalised against a project when one is given (that project's backend), else against the default backend.</summary>
        public Task<NormalizeResponse> NormalizeFiltersAsync(IEnumerable<string> names, string project = null, bool bayer = false, CancellationToken ct = default) =>
            (string.IsNullOrEmpty(project) ? ForDefault() : ForProject(project)).NormalizeFiltersAsync(names, project, bayer, ct);

        // Project-scoped: the project's own backend.
        public Task<ProjectDetail> GetProjectAsync(string project, CancellationToken ct = default) => ForProject(project).GetProjectAsync(project, ct);
        public Task<Membership> JoinProjectAsync(string project, JoinRequest request, CancellationToken ct = default) => ForProject(project).JoinProjectAsync(project, request, ct);
        public Task<Membership> GetMembershipAsync(string project, CancellationToken ct = default) => ForProject(project).GetMembershipAsync(project, ct);
        public Task<Membership> UpdateMembershipAsync(string project, UpdateMembershipRequest request, CancellationToken ct = default) => ForProject(project).UpdateMembershipAsync(project, request, ct);
        public Task<ProjectStatusDoc> GetProjectStatusAsync(string project, CancellationToken ct = default) => ForProject(project).GetProjectStatusAsync(project, ct);
        public Task<MosaicState> GetMosaicAsync(string project, CancellationToken ct = default) => ForProject(project).GetMosaicAsync(project, ct);
        public Task<MosaicState> PutClaimsAsync(string project, IReadOnlyList<string> panels, CancellationToken ct = default) => ForProject(project).PutClaimsAsync(project, panels, ct);
        public Task<FrameInitResponse> InitFrameAsync(string project, FrameInitRequest request, CancellationToken ct = default) => ForProject(project).InitFrameAsync(project, request, ct);
        public Task<FrameCompleteResponse> CompleteFrameAsync(string project, string frameId, IReadOnlyList<CompletedPart> etags, CancellationToken ct = default) => ForProject(project).CompleteFrameAsync(project, frameId, etags, ct);
        public Task<FrameListResponse> ListFramesAsync(string project, FrameListQuery query = null, CancellationToken ct = default) => ForProject(project).ListFramesAsync(project, query, ct);
        public Task<Frame> GetFrameAsync(string project, string frameId, CancellationToken ct = default) => ForProject(project).GetFrameAsync(project, frameId, ct);
        public Task<WithdrawResponse> WithdrawFrameAsync(string project, string frameId, CancellationToken ct = default) => ForProject(project).WithdrawFrameAsync(project, frameId, ct);
    }
}
