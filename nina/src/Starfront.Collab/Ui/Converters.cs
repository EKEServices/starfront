using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace Starfront.Collab.Ui {

    /// <summary>True shows, false collapses.</summary>
    public sealed class ShowWhenTrue : IValueConverter {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
            value is true ? Visibility.Visible : Visibility.Collapsed;
        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
    }

    /// <summary>False shows, true collapses.</summary>
    public sealed class ShowWhenFalse : IValueConverter {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
            value is true ? Visibility.Collapsed : Visibility.Visible;
        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
    }

    /// <summary>A non-empty string or collection shows; nothing collapses.</summary>
    public sealed class ShowWhenSomething : IValueConverter {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) {
            if (value == null) return Visibility.Collapsed;
            if (value is string text) return text.Trim().Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            if (value is System.Collections.ICollection list) return list.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            return Visibility.Visible;
        }
        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
    }
}
