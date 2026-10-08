using System.Reflection;

namespace Starfront.Collab.Sequencer {

    /// <summary>
    /// Builds the host's own instructions and triggers from whichever public
    /// constructor the running N.I.N.A. has.
    ///
    /// The plugin is compiled against one N.I.N.A. and runs in others. A
    /// `new DitherAfterExposures(guider, history, profile)` binds to that
    /// exact constructor at compile time, and N.I.N.A. 3.3 added a safety
    /// monitor to it, so on 3.3 the call threw MissingMethodException the
    /// moment a panel was built. Here the constructor is chosen at run time:
    /// the one with the most parameters that can all be filled from the
    /// services the plugin holds. A host that wants a service the plugin
    /// does not have fails with a message that names it, which beats a
    /// method-not-found from inside the runtime.
    /// </summary>
    internal static class HostItems {

        public static T Make<T>(params object[] services) where T : class {
            var constructors = typeof(T).GetConstructors(BindingFlags.Public | BindingFlags.Instance)
                .OrderByDescending(c => c.GetParameters().Length)
                .ToList();
            var missing = new List<string>();
            foreach (var constructor in constructors) {
                var parameters = constructor.GetParameters();
                var arguments = new object[parameters.Length];
                var complete = true;
                foreach (var (parameter, index) in parameters.Select((p, i) => (p, i))) {
                    var match = services.FirstOrDefault(s => s != null && parameter.ParameterType.IsInstanceOfType(s));
                    if (match == null) {
                        complete = false;
                        missing.Add(parameter.ParameterType.Name);
                        break;
                    }
                    arguments[index] = match;
                }
                if (!complete) continue;
                return (T)constructor.Invoke(arguments);
            }
            throw new NotSupportedException(
                $"This N.I.N.A. builds {typeof(T).Name} with a service the Starfront plugin does not have "
                + $"({string.Join(", ", missing.Distinct())}); the plugin needs updating for this N.I.N.A. version");
        }
    }
}
