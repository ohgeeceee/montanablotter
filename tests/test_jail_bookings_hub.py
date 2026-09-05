from pathlib import Path

from flask import Flask

from blueprints.detention import register_detention_blueprint


ROOT = Path(__file__).resolve().parents[1]


def build_app():
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    register_detention_blueprint(
        app,
        get_db=lambda: None,
        booking_context_loader=lambda *_args, **_kwargs: {},
        roster_directory_loader=lambda: {},
    )
    return app


def test_legacy_detention_pages_redirect_to_single_booking_hub():
    client = build_app().test_client()
    for path in ("/detention", "/jail-rosters"):
        response = client.get(path)
        assert response.status_code == 301
        assert response.headers["Location"] == "/jail-bookings"


def test_booking_hub_contains_map_fallback_and_detail_link():
    source = (ROOT / "templates" / "jail_bookings.html").read_text(encoding="utf-8")
    assert 'id="jail-county-map"' in source
    assert 'id="jail-map-county-select"' in source
    assert "<noscript>" in source
    assert 'href="/booking/{{ row.id }}"' in source
    assert "More booking information" in source


def test_map_script_uses_local_geojson_and_accessible_svg():
    source = (ROOT / "static" / "jail-bookings-map.js").read_text(encoding="utf-8")
    assert "data-geojson-url" in source
    assert "Interactive map of Montana counties" in source
    assert "aria-label" in source
    assert "window.location.assign" in source
