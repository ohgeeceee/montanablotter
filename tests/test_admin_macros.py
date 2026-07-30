"""Smoke tests for the admin component macros in templates/admin/_macros.html.

These tests render each macro through the live Flask app's Jinja environment
and assert the resulting HTML contains the expected class signatures. They
catch structural regressions when macros are edited.
"""
import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class AdminMacrosTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-macros-', suffix='.db')
        os.close(fd)
        self._prev_db_paths = [
            (config.DB_PATH, init_db.DB_PATH, app_module.config.DB_PATH)
        ]
        for mod in (config, init_db, app_module.config):
            mod.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True
        init_db.init_database()
        init_db.migrate()

    def tearDown(self) -> None:
        prev = self._prev_db_paths[0]
        for mod, path in zip((config, init_db, app_module.config), prev):
            mod.DB_PATH = path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _render(self, template_src: str) -> str:
        """Render a one-off template using the Flask app's Jinja environment.

        We wrap the original loader so that {% extends "admin/_macros.html" %}
        can still find the file on disk, while the top-level template is loaded
        from an in-memory dict.
        """
        from jinja2 import ChoiceLoader, DictLoader
        with app_module.app.test_request_context('/admin'):
            original_loader = app_module.app.jinja_env.loader
            app_module.app.jinja_env.loader = ChoiceLoader([
                DictLoader({'t': template_src}),
                original_loader,
            ])
            try:
                tpl = app_module.app.jinja_env.get_template('t')
                return tpl.render()
            finally:
                app_module.app.jinja_env.loader = original_loader

    def test_stat_macro_renders_label_and_value(self) -> None:
        html = self._render('{% from "admin/_macros.html" import stat %}{{ stat("Total", 42) }}')
        self.assertIn('adm-stat', html)
        self.assertIn('Total', html)
        self.assertIn('42', html)

    def test_stat_macro_supports_accent(self) -> None:
        html = self._render('{% from "admin/_macros.html" import stat %}{{ stat("Failed", 3, accent="red") }}')
        self.assertIn('adm-stat__value--danger', html)
        self.assertIn('3', html)

    def test_status_pill_auto_picks_color(self) -> None:
        html = self._render('{% from "admin/_macros.html" import status_pill %}{{ status_pill("active") }}')
        self.assertIn('adm-pill adm-pill--green', html)
        self.assertIn('active', html)

    def test_status_pill_failed_picks_red(self) -> None:
        html = self._render('{% from "admin/_macros.html" import status_pill %}{{ status_pill("failed") }}')
        self.assertIn('adm-pill adm-pill--red', html)

    def test_status_pill_pending_picks_amber(self) -> None:
        html = self._render('{% from "admin/_macros.html" import status_pill %}{{ status_pill("pending") }}')
        self.assertIn('adm-pill adm-pill--amber', html)

    def test_btn_renders_anchor(self) -> None:
        html = self._render('{% from "admin/_macros.html" import btn %}{{ btn("Save", href="/admin/save", variant="primary") }}')
        self.assertIn('adm-btn adm-btn--primary', html)
        self.assertIn('href="/admin/save"', html)
        self.assertIn('Save', html)

    def test_btn_renders_button(self) -> None:
        html = self._render('{% from "admin/_macros.html" import btn %}{{ btn("Submit", type="submit", variant="danger") }}')
        self.assertIn('adm-btn adm-btn--danger', html)
        self.assertIn('type="submit"', html)
        self.assertIn('Submit', html)

    def test_form_field_renders_input(self) -> None:
        html = self._render('{% from "admin/_macros.html" import form_field %}{{ form_field("email", "Email", type="email", value="x@y.com", required=True) }}')
        self.assertIn('adm-label-form', html)
        self.assertIn('adm-input', html)
        self.assertIn('name="email"', html)
        self.assertIn('type="email"', html)
        self.assertIn('value="x@y.com"', html)
        self.assertIn('required', html)

    def test_form_select_renders_options(self) -> None:
        html = self._render(
            '{% from "admin/_macros.html" import form_select %}'
            '{{ form_select("status", "Status", ["active", "queued"], value="active") }}'
        )
        self.assertIn('adm-select', html)
        self.assertIn('name="status"', html)
        self.assertIn('value="active"', html)
        self.assertIn('value="queued"', html)
        self.assertIn('selected', html)
        self.assertIn('active', html)
        self.assertIn('queued', html)

    def test_shortcut_card_renders_link(self) -> None:
        html = self._render(
            '{% from "admin/_macros.html" import shortcut_card %}'
            '{{ shortcut_card("Ingestion", "Health", href="/admin/ingestion", accent="amber") }}'
        )
        self.assertIn('adm-cmd adm-cmd--amber', html)
        self.assertIn('href="/admin/ingestion"', html)
        self.assertIn('Ingestion', html)
        self.assertIn('Health', html)


if __name__ == '__main__':
    unittest.main()
