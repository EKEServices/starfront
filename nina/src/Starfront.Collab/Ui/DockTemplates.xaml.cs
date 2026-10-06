using System.ComponentModel.Composition;
using System.Windows;

namespace Starfront.Collab.Ui {

    [Export(typeof(ResourceDictionary))]
    public partial class DockTemplates : ResourceDictionary {
        public DockTemplates() {
            InitializeComponent();
        }
    }
}
