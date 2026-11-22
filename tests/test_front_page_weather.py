"""Front-page Helena weather panel.

The masthead stats panel renders np_weather_temp / np_weather_sky on every
public page. These tests pin the failure-safe behavior of the Open-Meteo fetch
and its 30-minute in-process cache, plus the double-unit regression (the panel
used to render the literal '—°F °F · Clear' fallback).
"""
import unittest

import app as app_module


class HelenaWeatherTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the in-process cache so each test starts cold.
        app_module._WEATHER_CACHE['payload'] = None
        app_module._WEATHER_CACHE['fetched_at'] = 0.0

    def _fake_payload(self, **overrides):
        payload = {
            'current': {'temperature_2m': 32.7, 'weather_code': 3},
            'daily': {'sunrise': ['2026-11-16T07:32'], 'sunset': ['2026-11-16T16:53']},
        }
        payload.update(overrides)
        return payload

    def test_live_fetch_returns_fahrenheit_and_label(self):
        import json

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(self._payload).encode()

        captured = {}

        def _urlopen(req, timeout=None):
            captured['url'] = req.full_url if hasattr(req, 'full_url') else req
            captured['timeout'] = timeout
            r = _Resp()
            r._payload = self._fake_payload()
            return r

        orig = app_module.urllib.request.urlopen
        app_module.urllib.request.urlopen = _urlopen
        try:
            out = app_module._helena_weather()
        finally:
            app_module.urllib.request.urlopen = orig

        self.assertEqual(out['np_weather_temp'], '33°F')
        self.assertEqual(out['np_weather_sky'], 'Overcast')
        self.assertEqual(out['np_sunrise'], '7:32 AM')
        self.assertEqual(out['np_sunset'], '4:53 PM')
        self.assertIn('temperature_unit=fahrenheit', captured.get('url', ''))
        self.assertIn('America%2FDenver', captured.get('url', ''))

    def test_cache_avoids_second_network_call(self):
        calls = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                import json as _j
                return _j.dumps(self._payload).encode()

        def _urlopen(req, timeout=None):
            calls.append(req)
            r = _Resp()
            r._payload = self._fake_payload()
            return r

        orig = app_module.urllib.request.urlopen
        app_module.urllib.request.urlopen = _urlopen
        try:
            first = app_module._helena_weather()
            second = app_module._helena_weather()
        finally:
            app_module.urllib.request.urlopen = orig

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_upstream_failure_is_swallowed(self):
        def _boom(req, timeout=None):
            raise OSError('upstream down')

        orig = app_module.urllib.request.urlopen
        app_module.urllib.request.urlopen = _boom
        try:
            self.assertEqual(app_module._helena_weather(), {})
        finally:
            app_module.urllib.request.urlopen = orig

    def test_missing_temperature_returns_nothing(self):
        import json as _j

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _j.dumps({'current': {}}).encode()

        orig = app_module.urllib.request.urlopen
        app_module.urllib.request.urlopen = lambda req, timeout=None: _Resp()
        try:
            self.assertEqual(app_module._helena_weather(), {})
        finally:
            app_module.urllib.request.urlopen = orig

    def test_newspaper_stats_merges_weather(self):
        payload = self._fake_payload()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                import json as _j
                return _j.dumps(payload).encode()

        orig = app_module.urllib.request.urlopen
        app_module.urllib.request.urlopen = lambda req, timeout=None: _Resp()
        try:
            stats = app_module._newspaper_stats()
        finally:
            app_module.urllib.request.urlopen = orig

        self.assertEqual(stats['np_weather_temp'], '33°F')
        self.assertEqual(stats['np_weather_sky'], 'Overcast')
        self.assertTrue(stats['np_edition_date'])
        self.assertNotIn('—', stats['np_weather_temp'])

    def test_iso_hour_minute_formats_12h(self):
        self.assertEqual(app_module._iso_hour_minute('2026-11-16T07:32'), '7:32 AM')
        self.assertEqual(app_module._iso_hour_minute('2026-11-16T16:53'), '4:53 PM')
        self.assertEqual(app_module._iso_hour_minute('2026-11-16T00:05'), '12:05 AM')
        self.assertEqual(app_module._iso_hour_minute('2026-11-16T12:00'), '12:00 PM')
        self.assertEqual(app_module._iso_hour_minute(None), '')
        self.assertEqual(app_module._iso_hour_minute(''), '')


if __name__ == '__main__':
    unittest.main()
