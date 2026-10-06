using NINA.Astrometry;
using NINA.Astrometry.Interfaces;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;

namespace Starfront.Collab.Sky {

    /// <summary>Tonight's Moon at this site: how much is lit, and how much of the dark it is up for.</summary>
    public sealed record MoonTonight(double Illumination, double UpFraction);

    /// <summary>
    /// The little astronomy the plugin needs that N.I.N.A. does not hand over
    /// ready-made: which night it is, what the Moon is doing tonight, and
    /// where a panel stands in the sky right now.
    /// </summary>
    public static class SkyMath {

        /// <summary>
        /// The date a night belongs to, the way the server and Starfront name
        /// nights: frames taken after midnight belong to the evening before.
        /// Noon is the cut.
        /// </summary>
        public static string NightName(DateTime local) {
            var day = local.Hour >= 12 ? local.Date : local.Date.AddDays(-1);
            return day.ToString("yyyy-MM-dd");
        }

        public static ObserverInfo Observer(IProfileService profiles) {
            var astro = profiles.ActiveProfile.AstrometrySettings;
            return new ObserverInfo { Latitude = astro.Latitude, Longitude = astro.Longitude, Elevation = astro.Elevation };
        }

        /// <summary>
        /// Tonight's Moon. Sampled every ten minutes between astronomical dusk
        /// and dawn, because what matters to the choice of filter is how much
        /// of the *dark* the Moon is above the horizon for.
        /// </summary>
        public static MoonTonight Moon(IProfileService profiles, INighttimeCalculator calculator) {
            try {
                var observer = Observer(profiles);
                var data = calculator.Calculate();
                var (dusk, dawn) = DarkHours(data);
                if (dawn <= dusk) return null;
                var illumination = data.Illumination ?? AstroUtil.GetMoonIllumination(data.ReferenceDate, observer);
                int samples = 0, up = 0;
                for (var t = dusk; t <= dawn; t = t.AddMinutes(10)) {
                    samples++;
                    if (AstroUtil.GetMoonAltitude(t, observer) > 0.0) up++;
                }
                if (samples == 0) return null;
                return new MoonTonight(Math.Round(Math.Max(0.0, Math.Min(1.0, illumination)), 3),
                                       Math.Round(up / (double)samples, 3));
            } catch (Exception ex) {
                Logger.Error("Starfront: the Moon could not be worked out", ex);
                return null;
            }
        }

        /// <summary>Astronomical dusk and dawn for the night in hand, nautical when the sky never gets fully dark.</summary>
        public static (DateTime dusk, DateTime dawn) DarkHours(NighttimeData data) {
            foreach (var twilight in new[] { data.TwilightRiseAndSet, data.NauticalTwilightRiseAndSet, data.CivilTwilightRiseAndSet }) {
                if (twilight?.Rise is DateTime rise && twilight.Set is DateTime set) {
                    var dusk = set;
                    var dawn = rise;
                    if (dusk > dawn) dusk = dusk.AddDays(-1);
                    if (dawn - dusk > TimeSpan.FromHours(20)) dawn = dawn.AddDays(-1);
                    if (dawn > dusk) return (dusk, dawn);
                }
            }
            // Midsummer at high latitude, or no answer at all: call it evening to morning.
            var reference = data.ReferenceDate;
            return (reference.AddHours(9), reference.AddHours(18));
        }

        /// <summary>Where a point on the sky stands now: altitude and azimuth in degrees.</summary>
        public static (double altitude, double azimuth) AltAz(Coordinates coordinates, IProfileService profiles, DateTime? at = null) {
            var astro = profiles.ActiveProfile.AstrometrySettings;
            var topo = coordinates.Transform(Angle.ByDegree(astro.Latitude), Angle.ByDegree(astro.Longitude), astro.Elevation, at ?? DateTime.Now);
            return (topo.Altitude.Degree, topo.Azimuth.Degree);
        }

        /// <summary>The profile's horizon at an azimuth, or zero without one.</summary>
        public static double HorizonAltitude(IProfileService profiles, double azimuth) {
            try {
                return profiles.ActiveProfile.AstrometrySettings.Horizon?.GetAltitude(azimuth) ?? 0.0;
            } catch (Exception) {
                return 0.0;
            }
        }

        /// <summary>Hour angle in hours, 0..24: under 12 means west of the meridian, setting.</summary>
        public static double HourAngleHours(Coordinates coordinates, IProfileService profiles, DateTime? at = null) {
            var lst = AstroUtil.GetLocalSiderealTime(at ?? DateTime.Now, profiles.ActiveProfile.AstrometrySettings.Longitude);
            return AstroUtil.GetHourAngle(lst, coordinates.Transform(Epoch.JNOW).RA);
        }

        /// <summary>Degrees between a point and the Moon right now, and whether the Moon is up.</summary>
        public static (double separation, bool moonUp) MoonSeparation(Coordinates coordinates, IProfileService profiles) {
            var observer = Observer(profiles);
            var now = DateTime.Now;
            var moon = AstroUtil.GetMoonPosition(now, AstroUtil.GetJulianDate(now), observer);
            var moonCoordinates = new Coordinates(Angle.ByHours(moon.RA), Angle.ByDegree(moon.Dec), Epoch.JNOW);
            var separation = (coordinates.Transform(Epoch.JNOW) - moonCoordinates).Distance.Degree;
            var up = AstroUtil.GetMoonAltitude(now, observer) > 0.0;
            return (separation, up);
        }
    }
}
