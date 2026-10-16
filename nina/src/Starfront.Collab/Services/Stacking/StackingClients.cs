using System.Net;
using System.Net.Http;
using System.Reflection;
using RapidStackerCollab.Core.Api;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Services.Stacking {

    /// <summary>
    /// The connections to Rapid Stacker Collab: one handler, an API client
    /// with a timeout and a part-upload client without one (a 16 MB part on a
    /// slow link takes as long as it takes; the engine has its own per-part
    /// limit). Made when live stacking is turned on and thrown away when it is
    /// turned off, so a plugin with the setting off holds no connection.
    /// </summary>
    internal sealed class StackingClients : IDisposable {
        public StackingClients(StarfrontSettings settings) {
            var handler = new SocketsHttpHandler {
                PooledConnectionLifetime = TimeSpan.FromMinutes(5),
                AutomaticDecompression = DecompressionMethods.All,
            };
            ApiHttp = new HttpClient(handler, disposeHandler: true) { Timeout = TimeSpan.FromSeconds(60) };
            S3Http = new HttpClient(handler, disposeHandler: false) { Timeout = Timeout.InfiniteTimeSpan };
            var version = Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "0";
            Api = new ApiClient(ApiHttp, new SettingsTokenStore(settings), new ApiClientOptions {
                BaseUri = new Uri(settings.StackingApiBase),
                UserAgent = $"Starfront-NINA/{version}",
            });
        }

        public HttpClient ApiHttp { get; }
        public HttpClient S3Http { get; }
        public ApiClient Api { get; }

        public void Dispose() {
            S3Http.Dispose();
            ApiHttp.Dispose();
        }
    }
}
