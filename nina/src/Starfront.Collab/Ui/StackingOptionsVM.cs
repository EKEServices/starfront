using System.Windows.Input;
using CommunityToolkit.Mvvm.Input;
using NINA.Core.Utility;
using RapidStackerCollab.Core.Upload;
using Starfront.Collab.Services.Stacking;
using Starfront.Collab.Settings;

namespace Starfront.Collab.Ui {

    /// <summary>What the Live stacking section of the options page binds to.</summary>
    public sealed class StackingOptionsVM : BaseINPC {
        /// <summary>The upload modes on offer, in the order the list shows them. The code is what the setting stores.</summary>
        public static readonly IReadOnlyList<ModeChoice> ModeChoices = new[] {
            new ModeChoice(UploadMode.WhenIdle.ToString(), "When the camera is idle"),
            new ModeChoice(UploadMode.Immediately.ToString(), "As soon as a frame is saved"),
            new ModeChoice(UploadMode.TimeWindow.ToString(), "Only inside a daily time window"),
        };

        public sealed record ModeChoice(string Code, string Label);

        private readonly StackingUploader stacking;

        public StackingOptionsVM(StarfrontSettings settings, StackingUploader stacking) {
            Settings = settings;
            this.stacking = stacking;
            SignInCommand = new AsyncRelayCommand(SignInAsync, () => Settings.StackingEnabled && !SignIn.Busy);
            SignOutCommand = new AsyncRelayCommand(SignOutAsync, () => !SignIn.Busy);
            CancelSignInCommand = new CommunityToolkit.Mvvm.Input.RelayCommand(SignIn.Cancel, () => SignIn.Busy);
            OpenBrowserCommand = new CommunityToolkit.Mvvm.Input.RelayCommand(SignIn.OpenBrowser, () => SignIn.Waiting);
            SignIn.PropertyChanged += (_, e) => {
                if (e.PropertyName is nameof(StackingSignIn.Busy) or nameof(StackingSignIn.Waiting) or nameof(StackingSignIn.SignedIn)) Dispatch.OnUi(Refresh);
            };
            settings.PropertyChanged += (_, e) => {
                if (e.PropertyName is nameof(StarfrontSettings.StackingEnabled) or nameof(StarfrontSettings.StackingUploadMode) || string.IsNullOrEmpty(e.PropertyName)) Dispatch.OnUi(Refresh);
            };
        }

        public StarfrontSettings Settings { get; }
        public StackingSignIn SignIn => stacking.SignIn;
        public IReadOnlyList<ModeChoice> Modes => ModeChoices;

        /// <summary>The window rows are shown only when the mode uses them.</summary>
        public bool UsesWindow => Settings.StackingMode == UploadMode.TimeWindow;

        public AsyncRelayCommand SignInCommand { get; }
        public AsyncRelayCommand SignOutCommand { get; }
        public ICommand CancelSignInCommand { get; }
        public ICommand OpenBrowserCommand { get; }

        private void Refresh() {
            SignInCommand.NotifyCanExecuteChanged();
            SignOutCommand.NotifyCanExecuteChanged();
            ((CommunityToolkit.Mvvm.Input.RelayCommand)CancelSignInCommand).NotifyCanExecuteChanged();
            ((CommunityToolkit.Mvvm.Input.RelayCommand)OpenBrowserCommand).NotifyCanExecuteChanged();
            RaisePropertyChanged(nameof(UsesWindow));
        }

        private async Task SignInAsync() {
            try {
                await SignIn.SignInAsync();
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: sign-in failed", ex);
                NINA.Core.Utility.Notification.Notification.ShowError($"Starfront stacking: {ex.Message}");
            }
        }

        private async Task SignOutAsync() {
            try {
                await SignIn.SignOutAsync();
            } catch (Exception ex) {
                Logger.Error("Starfront stacking: sign-out failed", ex);
            }
        }
    }
}
