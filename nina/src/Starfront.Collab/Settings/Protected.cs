using System.Security.Cryptography;
using System.Text;

namespace Starfront.Collab.Settings {

    /// <summary>
    /// Credentials at rest, protected with the Windows data protection API so
    /// that the settings file is readable only by the Windows account that
    /// wrote it. A token copied to another machine or account decrypts to
    /// nothing, which is treated as "signed out" rather than as an error.
    /// </summary>
    internal static class Protected {
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Starfront.Collab/1");

        public static string Wrap(string secret) {
            if (string.IsNullOrEmpty(secret)) return "";
            var bytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(secret), Entropy, DataProtectionScope.CurrentUser);
            return Convert.ToBase64String(bytes);
        }

        public static string Unwrap(string wrapped) {
            if (string.IsNullOrEmpty(wrapped)) return "";
            try {
                var bytes = ProtectedData.Unprotect(Convert.FromBase64String(wrapped), Entropy, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(bytes);
            } catch (Exception) {
                return "";
            }
        }
    }
}
