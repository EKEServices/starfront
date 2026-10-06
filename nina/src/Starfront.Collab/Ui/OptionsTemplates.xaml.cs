using System.ComponentModel.Composition;
using System.Reflection;
using System.Windows;

namespace Starfront.Collab.Ui {

    /// <summary>
    /// The options page. N.I.N.A. finds it by the key "&lt;plugin title&gt;_Options",
    /// so the key is derived from the assembly's title rather than typed twice.
    /// </summary>
    [Export(typeof(ResourceDictionary))]
    public partial class OptionsTemplates : ResourceDictionary {
        public OptionsTemplates() {
            InitializeComponent();
            var title = typeof(OptionsTemplates).Assembly.GetCustomAttribute<AssemblyTitleAttribute>()?.Title ?? "Starfront Collaboration";
            this[title + "_Options"] = this["Starfront_OptionsTemplate"];
            Remove("Starfront_OptionsTemplate");
        }
    }
}
