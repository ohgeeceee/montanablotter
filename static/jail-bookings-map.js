(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  function normalizeCountyName(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/\bcounty\b/g, '')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function ringsForGeometry(geometry) {
    if (!geometry) return [];
    if (geometry.type === 'Polygon') return geometry.coordinates;
    if (geometry.type === 'MultiPolygon') {
      return geometry.coordinates.reduce(function (all, polygon) {
        return all.concat(polygon);
      }, []);
    }
    return [];
  }

  function initialize() {
    var root = document.getElementById('jail-county-map');
    var dataNode = document.getElementById('jail-county-map-data');
    if (!root || !dataNode) return;

    var counties;
    try {
      counties = JSON.parse(dataNode.textContent || '[]');
    } catch (error) {
      root.textContent = 'County map data could not be loaded. Use the county list instead.';
      return;
    }

    var status = document.getElementById('jail-map-status');
    var selector = document.getElementById('jail-map-county-select');
    var byName = {};
    counties.forEach(function (county) {
      byName[normalizeCountyName(county.name)] = county;
    });

    if (selector) {
      selector.addEventListener('change', function () {
        if (selector.value) window.location.assign(selector.value);
      });
    }

    function describe(county) {
      if (!status || !county) return;
      status.innerHTML = '';
      var name = document.createElement('strong');
      name.className = 'block text-lg';
      name.textContent = county.name + ' County';
      var detail = document.createElement('span');
      detail.className = 'mt-1 block text-sm text-slate-300';
      detail.textContent = county.status_label;
      status.appendChild(name);
      status.appendChild(detail);
      if (county.href) {
        var prompt = document.createElement('span');
        prompt.className = 'mt-2 block text-xs font-black uppercase tracking-wider text-orange-300';
        prompt.textContent = 'Click to view recent bookings';
        status.appendChild(prompt);
      }
    }

    fetch(root.getAttribute('data-geojson-url'), { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('Map request failed');
        return response.json();
      })
      .then(function (geojson) {
        var features = geojson.features || [];
        var projected = [];
        var latitudeScale = Math.cos(47 * Math.PI / 180);
        features.forEach(function (feature) {
          ringsForGeometry(feature.geometry).forEach(function (ring) {
            ring.forEach(function (point) {
              projected.push([point[0] * latitudeScale, point[1]]);
            });
          });
        });
        if (!projected.length) throw new Error('No county geometry');

        var minX = Math.min.apply(null, projected.map(function (p) { return p[0]; }));
        var maxX = Math.max.apply(null, projected.map(function (p) { return p[0]; }));
        var minY = Math.min.apply(null, projected.map(function (p) { return p[1]; }));
        var maxY = Math.max.apply(null, projected.map(function (p) { return p[1]; }));
        var width = 960;
        var height = 540;
        var pad = 18;
        var scale = Math.min((width - pad * 2) / (maxX - minX), (height - pad * 2) / (maxY - minY));
        var offsetX = (width - (maxX - minX) * scale) / 2;
        var offsetY = (height - (maxY - minY) * scale) / 2;

        function xy(point) {
          return [
            offsetX + (point[0] * latitudeScale - minX) * scale,
            offsetY + (maxY - point[1]) * scale
          ];
        }

        var svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'Interactive map of Montana counties with current jail booking feeds');

        features.forEach(function (feature) {
          var county = byName[normalizeCountyName(feature.properties && feature.properties.NAME)];
          if (!county) return;
          var pathData = ringsForGeometry(feature.geometry).map(function (ring) {
            return ring.map(function (point, index) {
              var value = xy(point);
              return (index ? 'L' : 'M') + value[0].toFixed(2) + ',' + value[1].toFixed(2);
            }).join(' ') + ' Z';
          }).join(' ');
          var path = document.createElementNS(SVG_NS, 'path');
          path.setAttribute('d', pathData);
          path.setAttribute('fill-rule', 'evenodd');
          path.setAttribute('class', 'mb-jail-county mb-jail-county--' + county.state);
          var title = document.createElementNS(SVG_NS, 'title');
          title.textContent = county.name + ' County: ' + county.status_label;
          path.appendChild(title);

          var target = path;
          if (county.href) {
            var link = document.createElementNS(SVG_NS, 'a');
            link.setAttribute('href', county.href);
            link.setAttribute('class', 'mb-jail-county-link');
            link.setAttribute('aria-label', county.name + ' County, ' + county.status_label + '. View bookings.');
            link.appendChild(path);
            target = link;
          } else {
            path.setAttribute('tabindex', '0');
            path.setAttribute('class', path.getAttribute('class') + ' mb-jail-county-focus');
            path.setAttribute('aria-label', county.name + ' County, ' + county.status_label);
          }
          target.addEventListener('mouseenter', function () { describe(county); });
          target.addEventListener('focus', function () { describe(county); });
          svg.appendChild(target);
        });

        root.innerHTML = '';
        root.appendChild(svg);
      })
      .catch(function () {
        root.innerHTML = '<p class="p-6 text-sm text-slate-600">The county map could not load. Choose a county from the list beside it.</p>';
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
}());
