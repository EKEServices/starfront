/* Starfront sky: where the observatory's telescopes are pointing tonight.
 *
 * Drop-in for any web page. Put an element on the page and this script
 * beside it:
 *
 *   <div data-starfront-sky></div>
 *   <script src="https://collab.starfront.space/sky/widget.js" async></script>
 *
 * It draws the whole sky as a chart - right ascension across, declination
 * down, the bright stars in place - and on it every telescope that shares
 * its position: a mark where it points now, its name and target, and the
 * track it has drawn across the sky through the night. The part of the sky
 * below the observatory's horizon right now is shaded, so the chart reads
 * as "what is up" rather than as a map.
 *
 * Options go on the element as data attributes:
 *   data-server="https://collab.starfront.space"  where to ask (default: this script's own host)
 *   data-hours="14"      how far back the tracks go
 *   data-refresh="60"    seconds between refreshes
 *   data-lat="31.9" data-lon="-99.1"  the observatory, for the horizon shading
 *   data-title="Tonight at Starfront"  the heading; blank for none
 *   data-height="auto"   pixels, or "auto" for half the width
 *
 * Everything it asks for is public and read-only. Nothing is written
 * anywhere, nothing is stored in the visitor's browser.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  var scriptOrigin = '';
  try { scriptOrigin = script && script.src ? new URL(script.src).origin : ''; } catch (e) { scriptOrigin = ''; }

  var STATE_COLOURS = {
    imaging: '#5be39a', exposing: '#5be39a', slewing: '#f0b35b', centring: '#f0b35b',
    centering: '#f0b35b', focusing: '#8ec5ff', solving: '#8ec5ff', guiding: '#5be39a',
    flipping: '#f0b35b', idle: '#8a93a6', parked: '#8a93a6', cooling: '#8ec5ff'
  };

  function palette(index) {
    var hues = [152, 32, 205, 280, 0, 60, 330, 100, 180, 20, 240, 300];
    return 'hsl(' + hues[index % hues.length] + ', 75%, 65%)';
  }

  /* ---------------------------------------------------------- astronomy */

  // Greenwich mean sidereal time in hours, good to a few seconds.
  function gmstHours(date) {
    var jd = date.getTime() / 86400000 + 2440587.5;
    var t = (jd - 2451545.0) / 36525.0;
    var gmst = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t;
    gmst = ((gmst % 360) + 360) % 360;
    return gmst / 15;
  }

  // Altitude in degrees of a point (RA hours, Dec degrees) from a site.
  function altitude(raHours, dec, latDeg, lstHours) {
    var ha = ((lstHours - raHours) * 15) * Math.PI / 180;
    var lat = latDeg * Math.PI / 180;
    var d = dec * Math.PI / 180;
    var s = Math.sin(lat) * Math.sin(d) + Math.cos(lat) * Math.cos(d) * Math.cos(ha);
    return Math.asin(Math.max(-1, Math.min(1, s))) * 180 / Math.PI;
  }

  /* ------------------------------------------------------------- widget */

  function build(host) {
    var opts = {
      server: host.getAttribute('data-server') || scriptOrigin || '',
      hours: Number(host.getAttribute('data-hours')) || 14,
      refresh: Math.max(15, Number(host.getAttribute('data-refresh')) || 60),
      lat: host.hasAttribute('data-lat') ? Number(host.getAttribute('data-lat')) : 31.9,
      lon: host.hasAttribute('data-lon') ? Number(host.getAttribute('data-lon')) : -99.1,
      title: host.hasAttribute('data-title') ? host.getAttribute('data-title') : 'Tonight at Starfront',
      height: host.getAttribute('data-height') || 'auto'
    };

    host.classList.add('starfront-sky');
    host.innerHTML = '';
    var style = document.createElement('style');
    style.textContent = [
      '.starfront-sky{font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#dfe5f0;background:#0b0e14;border-radius:10px;padding:14px 16px 12px;box-sizing:border-box;}',
      '.starfront-sky *{box-sizing:border-box;}',
      '.starfront-sky .sf-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 8px;}',
      '.starfront-sky .sf-title{font-size:18px;font-weight:600;margin:0;}',
      '.starfront-sky .sf-sub{color:#8a93a6;font-size:13px;}',
      '.starfront-sky canvas{display:block;width:100%;height:auto;border-radius:6px;background:#05070c;}',
      '.starfront-sky .sf-list{list-style:none;margin:10px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:4px 16px;}',
      '.starfront-sky .sf-list li{display:flex;align-items:center;gap:8px;min-width:0;font-size:13px;}',
      '.starfront-sky .sf-dot{width:10px;height:10px;border-radius:50%;flex:none;}',
      '.starfront-sky .sf-name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '.starfront-sky .sf-what{color:#8a93a6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '.starfront-sky .sf-off{opacity:.45;}',
      '.starfront-sky .sf-foot{color:#5c6577;font-size:11px;margin-top:8px;}'
    ].join('');
    host.appendChild(style);

    var head = document.createElement('div');
    head.className = 'sf-head';
    if (opts.title) {
      var title = document.createElement('h3');
      title.className = 'sf-title';
      title.textContent = opts.title;
      head.appendChild(title);
    }
    var sub = document.createElement('span');
    sub.className = 'sf-sub';
    sub.textContent = 'loading…';
    head.appendChild(sub);
    host.appendChild(head);

    var canvas = document.createElement('canvas');
    host.appendChild(canvas);
    var list = document.createElement('ul');
    list.className = 'sf-list';
    host.appendChild(list);
    var foot = document.createElement('div');
    foot.className = 'sf-foot';
    foot.textContent = 'Right ascension across, declination down. The shaded part of the sky is below the observatory’s horizon right now. Tracks show where each telescope has pointed through the night.';
    host.appendChild(foot);

    var stars = [];
    var data = null;
    var ctx = canvas.getContext('2d');

    function size() {
      var width = Math.max(240, host.clientWidth - 32);
      var height = opts.height === 'auto' ? Math.round(width / 2) : Math.max(120, Number(opts.height) || Math.round(width / 2));
      var ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.style.height = height + 'px';
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { w: width, h: height };
    }

    // Chart coordinates: RA 24h at the left edge down to 0h at the right,
    // as every sky chart has it; Dec +90 at the top.
    function place(raHours, dec, w, h) {
      var ra = ((raHours % 24) + 24) % 24;
      return { x: (24 - ra) / 24 * w, y: (90 - dec) / 180 * h };
    }

    function draw() {
      var box = size();
      var w = box.w, h = box.h;
      ctx.clearRect(0, 0, w, h);
      var sky = ctx.createLinearGradient(0, 0, 0, h);
      sky.addColorStop(0, '#070b16');
      sky.addColorStop(1, '#03050a');
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);

      // Grid: every 2 hours of RA, every 30 degrees of Dec.
      ctx.strokeStyle = 'rgba(120, 140, 180, 0.16)';
      ctx.lineWidth = 1;
      ctx.fillStyle = 'rgba(140, 155, 190, 0.6)';
      ctx.font = '10px system-ui, sans-serif';
      ctx.textAlign = 'center';
      for (var hr = 0; hr <= 24; hr += 2) {
        var p = place(hr, 0, w, h);
        ctx.beginPath(); ctx.moveTo(p.x + 0.5, 0); ctx.lineTo(p.x + 0.5, h); ctx.stroke();
        if (hr < 24) ctx.fillText(hr + 'h', p.x, h - 4);
      }
      ctx.textAlign = 'left';
      for (var dec = -60; dec <= 60; dec += 30) {
        var q = place(0, dec, w, h);
        ctx.beginPath(); ctx.moveTo(0, q.y + 0.5); ctx.lineTo(w, q.y + 0.5); ctx.stroke();
        ctx.fillText((dec > 0 ? '+' : '') + dec + '°', 3, q.y - 3);
      }

      // Stars, brightest largest.
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        var sp = place(s[0] / 15, s[1], w, h);
        var r = Math.max(0.5, (5.6 - s[2]) * 0.42);
        ctx.fillStyle = 'rgba(230, 236, 250, ' + Math.min(1, 0.35 + (5.5 - s[2]) * 0.18) + ')';
        ctx.beginPath(); ctx.arc(sp.x, sp.y, r, 0, Math.PI * 2); ctx.fill();
      }

      // What is below the horizon at the observatory right now.
      var lst = gmstHours(new Date()) + opts.lon / 15;
      var step = 3;                                   // degrees per cell
      ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
      for (var d = -90; d < 90; d += step) {
        for (var a = 0; a < 360; a += step) {
          if (altitude((a + step / 2) / 15, d + step / 2, opts.lat, lst) < 0) {
            var c0 = place((a + step) / 15, d + step, w, h);
            var c1 = place(a / 15, d, w, h);
            ctx.fillRect(c0.x, c0.y, c1.x - c0.x + 0.6, c1.y - c0.y + 0.6);
          }
        }
      }

      if (!data) return;
      var rows = data.telescopes || [];
      // Tracks first, under the marks.
      rows.forEach(function (t, index) {
        var colour = palette(index);
        var trail = t.trail || [];
        if (trail.length > 1) {
          ctx.lineWidth = 1.5;
          for (var k = 1; k < trail.length; k++) {
            var a0 = place(trail[k - 1][1], trail[k - 1][2], w, h);
            var a1 = place(trail[k][1], trail[k][2], w, h);
            // A slew across the meridian of the chart would draw a line
            // right across it; leave that one out.
            if (Math.abs(a1.x - a0.x) > w / 2) continue;
            var fade = 0.25 + 0.6 * (k / trail.length);
            ctx.strokeStyle = colour;
            ctx.globalAlpha = t.online ? fade : fade * 0.4;
            ctx.beginPath(); ctx.moveTo(a0.x, a0.y); ctx.lineTo(a1.x, a1.y); ctx.stroke();
          }
          ctx.globalAlpha = 1;
        }
      });
      rows.forEach(function (t, index) {
        var colour = palette(index);
        var p = place(t.ra, t.dec, w, h);
        ctx.globalAlpha = t.online ? 1 : 0.4;
        ctx.strokeStyle = colour;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = STATE_COLOURS[(t.state || '').toLowerCase()] || colour;
        ctx.beginPath(); ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#eef2fa';
        ctx.font = '600 11px system-ui, sans-serif';
        // The label sits to the right of the mark unless it would run off
        // the chart there, in which case it sits to the left.
        var label = t.name + (t.target ? ' · ' + t.target : '');
        var flip = p.x + 9 + ctx.measureText(label).width > w - 4;
        ctx.textAlign = flip ? 'right' : 'left';
        ctx.fillText(label, p.x + (flip ? -9 : 9), p.y + 4);
        ctx.globalAlpha = 1;
      });
      ctx.textAlign = 'left';
    }

    function describe(t) {
      var bits = [];
      if (t.target) bits.push(t.target);
      if (t.state) bits.push(t.state);
      if (!t.online) {
        var mins = Math.round((t.ageSeconds || 0) / 60);
        bits.push(mins < 120 ? mins + ' min ago' : Math.round(mins / 60) + ' h ago');
      }
      return bits.join(' · ');
    }

    function fill() {
      list.innerHTML = '';
      if (!data) return;
      var rows = data.telescopes || [];
      rows.forEach(function (t, index) {
        var li = document.createElement('li');
        if (!t.online) li.className = 'sf-off';
        var dot = document.createElement('span');
        dot.className = 'sf-dot';
        dot.style.background = palette(index);
        var name = document.createElement('span');
        name.className = 'sf-name';
        name.textContent = t.name;
        var what = document.createElement('span');
        what.className = 'sf-what';
        what.textContent = describe(t);
        li.appendChild(dot); li.appendChild(name); li.appendChild(what);
        list.appendChild(li);
      });
      var when = new Date((data.serverTime || Date.now() / 1000) * 1000);
      sub.textContent = (data.online || 0) + ' telescope' + (data.online === 1 ? '' : 's') + ' on the sky, '
        + (data.imaging || 0) + ' imaging · as of ' + when.toUTCString().slice(17, 22) + ' UTC';
    }

    function fetchJson(url) {
      return fetch(url, { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }

    function refresh() {
      fetchJson(opts.server + '/api/v1/sky?hours=' + encodeURIComponent(opts.hours))
        .then(function (payload) { data = payload; fill(); draw(); })
        .catch(function (err) { sub.textContent = 'the observatory is not answering right now'; draw(); });
    }

    fetchJson(opts.server + '/sky/stars.json')
      .then(function (payload) { stars = payload.stars || []; draw(); })
      .catch(function () { draw(); });
    refresh();
    setInterval(refresh, opts.refresh * 1000);
    // The horizon moves with the clock even when nothing else does.
    setInterval(draw, 60 * 1000);
    window.addEventListener('resize', draw);
    draw();
  }

  function start() {
    var hosts = document.querySelectorAll('[data-starfront-sky]');
    for (var i = 0; i < hosts.length; i++) {
      if (!hosts[i].getAttribute('data-sf-built')) {
        hosts[i].setAttribute('data-sf-built', '1');
        build(hosts[i]);
      }
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
