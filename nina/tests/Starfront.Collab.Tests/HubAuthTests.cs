using Microsoft.Data.Sqlite;
using NUnit.Framework;
using RapidStackerCollab.Core.Api;
using RapidStackerCollab.Core.Auth;
using RapidStackerCollab.Core.Fits;
using RapidStackerCollab.Core.Frames;
using RapidStackerCollab.Core.Tests.Fakes;
using RapidStackerCollab.Core.Tests.Support;
using RapidStackerCollab.Core.Upload;
using Starfront.Collab.Server;
using Starfront.Collab.Services.Stacking;

namespace Starfront.Collab.Tests {

    /// <summary>
    /// T607. The real runner and engine, behind the real router, with a hub that refuses its grant and a cloud
    /// that is the fake Rapid Stacker Collab server: a hub's 401 stays with its link, and each backend has its own rig id.
    /// </summary>
    [TestFixture]
    public class HubAuthTests {
        private const string HubProject = "hub-project-1";
        private const string Rig = "rig_cloud";

        private FakeCollabServer server = null!;
        private FakeClock clock = null!;
        private HttpClient http = null!;
        private string dir = null!;
        private UploadQueue queue = null!;
        private FakeRoutes routes = null!;
        private ScriptedClient hub = null!;
        private StackingLink hubLink = null!;
        private int seed;

        [SetUp]
        public async Task SetUp() {
            server = await FakeCollabServer.StartAsync();
            clock = new FakeClock();
            http = new HttpClient();
            dir = Directory.CreateTempSubdirectory("sf-hubauth-").FullName;
            Directory.CreateDirectory(Path.Combine(dir, "data"));
            queue = new UploadQueue(Path.Combine(dir, "queue.db"), clock);
            routes = new FakeRoutes { Cloud = new ApiClient(http, new InMemoryTokenStore(FakeCollabServer.Token), new ApiClientOptions { BaseUri = server.ApiBase }) };
            hubLink = routes.Add("s-hub", HubProject, "hub", "sfg_old.grant");
            hubLink.Status = LinkStatus.Active;
            var (client, scripted) = ScriptedClient.Make();
            hub = scripted;
            routes.PerLink["s-hub"] = client;
        }

        [TearDown]
        public async Task TearDown() {
            queue.Dispose();
            SqliteConnection.ClearAllPools();
            http.Dispose();
            await server.DisposeAsync();
            try { Directory.Delete(dir, true); } catch (IOException) { }
        }

        private UploadRunner Runner() => new(queue, new UploadEngine(new RoutingApiClient(routes), http, clock), new UploadPolicy(clock),
            () => new UploadPolicySettings(UploadMode.Immediately), () => new ImagingState(), clock,
            new UploadRunnerOptions { TempDirectory = Path.Combine(dir, "tmp"), StripPredicate = _ => true, StripLocation = true });

        private QueueItem Enqueue(string project) {
            var name = $"light{++seed}.fits";
            var path = Path.Combine(dir, "data", name);
            File.WriteAllBytes(path, FitsBuilder.NinaLight("Ha", 300 + seed, withLocation: true).Build());
            return queue.Enqueue(new NewQueueItem(project, path, name, FrameKind.Light, FitsHeaderReader.Read(path), Rig)).Item;
        }

        private void HubAnswers401() => hub.Script[nameof(IApiClient.InitFrameAsync)] =
            _ => throw new ApiException(ApiCodes.TokenInvalid, "The grant was not accepted.", 401);

        [Test]
        public async Task AHub401MidUploadClearsOnlyThatLinksGrantAndTheCloudKeepsUploading() {
            var hubItem = Enqueue(HubProject);
            var cloudItem = Enqueue(FakeCollabServer.ProjectId);
            HubAnswers401();
            var runner = Runner();
            var authRequired = 0;
            runner.OnAuthRequired = () => authRequired++;

            Assert.That(await runner.RunOnceAsync(), Is.EqualTo(2), "both items were attempted in the one pass");

            Assert.That(authRequired, Is.Zero, "the cloud sign-in is not asked for");
            Assert.That(runner.AuthRequired, Is.False);
            Assert.That(routes.Refused, Is.EqualTo(new[] { "s-hub" }));
            Assert.That(hubLink.Grant, Is.Empty, "that link's grant is gone");
            Assert.That(hubLink.Status, Is.EqualTo(LinkStatus.Failed));
            Assert.That(queue.IsPaused(HubProject), Is.True, "the hub project's items are held");
            Assert.That(queue.IsPaused(FakeCollabServer.ProjectId), Is.False);
            Assert.That(queue.Get(cloudItem.Id)!.State, Is.EqualTo(QueueStates.Uploaded), "the cloud item was sent");
            Assert.That(queue.Get(hubItem.Id)!.State, Is.EqualTo(QueueStates.Pending), "the hub item waits, it did not fail");

            // A cloud frame saved later still goes while the hub link waits.
            var later = Enqueue(FakeCollabServer.ProjectId);
            Assert.That(await runner.RunOnceAsync(), Is.EqualTo(1));
            Assert.That(queue.Get(later.Id)!.State, Is.Not.EqualTo(QueueStates.Pending));
            Assert.That(runner.AuthRequired, Is.False);
        }

        [Test]
        public async Task TheNextCheckInsGrantLiftsTheHold() {
            var hubItem = Enqueue(HubProject);
            HubAnswers401();
            await Runner().RunOnceAsync();
            Assert.That(queue.IsPaused(HubProject), Is.True);

            // Still no grant: nothing is lifted.
            Assert.That(HubAuth.ResumeRefreshed(queue, id => id == HubProject ? hubLink : null), Is.Zero);
            Assert.That(queue.IsPaused(HubProject), Is.True);

            // The check-in brings a new grant (the joiner stores it on the link).
            hubLink.Grant = "sfg_new.grant";
            Assert.That(HubAuth.ResumeRefreshed(queue, id => id == HubProject ? hubLink : null), Is.EqualTo(1));
            Assert.That(queue.IsPaused(HubProject), Is.False);
            Assert.That(queue.Get(hubItem.Id)!.State, Is.EqualTo(QueueStates.Pending));
        }

        [Test]
        public async Task AUsersOwnPauseOfAHubProjectIsNotLifted() {
            Enqueue(HubProject);
            queue.PauseProject(HubProject, "paused by the user", null, null, byUser: true);
            Assert.That(HubAuth.ResumeRefreshed(queue, _ => hubLink), Is.Zero);
            Assert.That(queue.IsPaused(HubProject), Is.True);
            await Task.CompletedTask;
        }

        [Test]
        public async Task ACloud401StillRaisesAuthRequiredAndLeavesTheHubAlone() {
            var cloud = ScriptedClient.Make();
            cloud.Item2.Script[nameof(IApiClient.InitFrameAsync)] = _ => throw new ApiException(ApiCodes.TokenInvalid, "Signed out.", 401);
            routes.Cloud = cloud.Item1;
            Enqueue(FakeCollabServer.ProjectId);
            var runner = Runner();
            var authRequired = 0;
            runner.OnAuthRequired = () => authRequired++;

            await runner.RunOnceAsync();

            Assert.That(authRequired, Is.EqualTo(1));
            Assert.That(runner.AuthRequired, Is.True);
            Assert.That(routes.Refused, Is.Empty);
            Assert.That(hubLink.Grant, Is.EqualTo("sfg_old.grant"));
            Assert.That(queue.IsPaused(HubProject), Is.False);
        }

        // -- one rig id per backend ---------------------------------------------------

        [Test]
        public async Task RigAndLibraryCallsCarryTheRigIdOfTheBackendTheyGoTo() {
            var (cloudClient, cloud) = ScriptedClient.Make();
            routes.Cloud = cloudClient;
            routes.CloudRig = "rig_cloud";
            hubLink.RigId = "rig_hub";
            var seen = new List<(string Who, string Rig)>();
            cloud.Script[nameof(IApiClient.GetLibraryAsync)] = a => { seen.Add(("cloud", (string)a[0])); return null!; };
            hub.Script[nameof(IApiClient.GetLibraryAsync)] = a => { seen.Add(("hub", (string)a[0])); return null!; };
            var router = new RoutingApiClient(routes);

            routes.DefaultBackend = "hub";
            await router.GetLibraryAsync("whatever the caller held");
            routes.DefaultBackend = "cloud";
            await router.GetLibraryAsync("whatever the caller held");

            Assert.That(seen, Is.EqualTo(new[] { ("hub", "rig_hub"), ("cloud", "rig_cloud") }));
        }

        [Test]
        public async Task ABackendWithNoRigYetKeepsTheCallersId() {
            var (cloudClient, cloud) = ScriptedClient.Make();
            routes.Cloud = cloudClient;
            var seen = new List<string>();
            hub.Script[nameof(IApiClient.PatchRigAsync)] = a => { seen.Add((string)a[0]); return new Rig(); };
            routes.DefaultBackend = "hub";
            await new RoutingApiClient(routes).PatchRigAsync("held", new RigPatchRequest());
            Assert.That(seen, Is.EqualTo(new[] { "held" }));
            await Task.CompletedTask;
        }

        [Test]
        public async Task AFrameInitToAHubProjectHintsTheHubsRigNotTheCloudsOne() {
            hubLink.RigId = "rig_hub";
            routes.CloudRig = "rig_cloud";
            FrameInitRequest got = null!;
            hub.Script[nameof(IApiClient.InitFrameAsync)] = a => { got = (FrameInitRequest)a[1]; return new FrameInitResponse { FrameId = "f1" }; };
            await new RoutingApiClient(routes).InitFrameAsync(HubProject, new FrameInitRequest { Filename = "a.fits", RigHint = new RigHint { RigId = "rig_cloud" } });
            Assert.That(got.RigHint!.RigId, Is.EqualTo("rig_hub"));
            Assert.That(got.Filename, Is.EqualTo("a.fits"));
        }

        [Test]
        public void TheRigIdsOfTheBackendsAreResolvedApart() {
            var a = new StackingLink { StarfrontProjectId = "a", Backend = "hub", HubId = "h1", RigId = "rig_h1", Grant = "g", ApiBase = "https://h/", CollabProjectId = "pa", Status = LinkStatus.Active };
            var b = new StackingLink { StarfrontProjectId = "b", Backend = "hub", HubId = "h1", Grant = "g", ApiBase = "https://h/", CollabProjectId = "pb", Status = LinkStatus.Active };
            var other = new StackingLink { StarfrontProjectId = "c", Backend = "hub", HubId = "h2", Grant = "g", ApiBase = "https://h2/", CollabProjectId = "pc", Status = LinkStatus.Active };
            var links = new[] { a, b, other };

            Assert.That(RigIds.OfHub(b, links), Is.EqualTo("rig_h1"), "a second collaboration on the same hub is the same rig there");
            Assert.That(RigIds.OfHub(other, links), Is.Empty, "another hub does not know it");
            Assert.That(RigIds.ForDefaultBackend("cloud", "rig_cloud", links), Is.EqualTo("rig_cloud"));
            Assert.That(RigIds.ForDefaultBackend("hub", "rig_cloud", links), Is.EqualTo("rig_h1"));
            Assert.That(RigIds.ForDefaultBackend("hub", "rig_cloud", Array.Empty<StackingLink>()), Is.EqualTo("rig_cloud"), "no usable hub: the cloud's");
            Assert.That(RigIds.ForDefaultBackend("cloud", "", links), Is.Empty, "no fallback to a hub's id for a cloud call");
            Assert.That(RigIds.LocalKey("cloud", "", links), Is.EqualTo("rig_h1"), "a hub-only telescope still groups its frames");
            Assert.That(RigIds.LocalKey("cloud", "rig_cloud", links), Is.EqualTo("rig_cloud"), "a cloud user sees no change");
        }
    }
}
