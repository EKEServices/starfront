using System.ComponentModel.Composition;
using System.Windows;

namespace Starfront.Collab.Sequencer {

    /// <summary>The sequencer's editors for the plugin's instructions, merged into the host's resources.</summary>
    [Export(typeof(ResourceDictionary))]
    public partial class SequencerTemplates : ResourceDictionary {
        public SequencerTemplates() {
            InitializeComponent();
        }
    }
}
