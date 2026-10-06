using System.Windows;

namespace Starfront.Collab.Ui {

    /// <summary>WPF objects belong to their dispatcher; mediator and poll callbacks arrive on any thread.</summary>
    internal static class Dispatch {
        public static void OnUi(Action action) {
            var dispatcher = Application.Current?.Dispatcher;
            if (dispatcher == null || dispatcher.CheckAccess()) action();
            else dispatcher.BeginInvoke(action);
        }
    }
}
