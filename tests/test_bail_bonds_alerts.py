import os
import sqlite3
import tempfile
import unittest

from bail_bonds_alerts import (
    check_for_felony_bookings,
    dispatch_felony_booking_alerts,
    ensure_bail_bonds_alert_schema,
)


class BailBondsAlertTests(unittest.TestCase):
    def _make_db(self) -> str:
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute(
            '''
            CREATE TABLE subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                phone TEXT DEFAULT ''
            )
            '''
        )
        ensure_bail_bonds_alert_schema(conn)
        conn.commit()
        conn.close()
        return path

    def test_matches_active_subscriber_by_county_and_charge_type(self) -> None:
        path = self._make_db()
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                '''
                INSERT INTO subscribers (
                    email,
                    token,
                    phone_number_sms,
                    agency_name,
                    counties_of_interest,
                    charge_types_of_interest,
                    subscription_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'dispatch@lisasfamilybailbonds.com',
                    'tok_1',
                    '406-555-1212',
                    "Lisa's Family Bail Bonds",
                    '["cascade"]',
                    '["felony","burglary"]',
                    'active',
                ),
            )
            conn.commit()
            conn.close()

            alerts = check_for_felony_bookings(
                [
                    {
                        'county_name': 'Cascade',
                        'person_name': 'John Doe',
                        'booking_at': '2026-04-03 09:00:00',
                        'charges_summary': 'Felony Burglary and Criminal Mischief',
                    }
                ],
                db_path=path,
            )

            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]['agency_name'], "Lisa's Family Bail Bonds")
            self.assertIn('felony', alerts[0]['matched_charge_types'])
            self.assertEqual(alerts[0]['phone_number_sms'], '+14065551212')
        finally:
            os.remove(path)

    def test_skips_subscriber_when_county_does_not_match(self) -> None:
        path = self._make_db()
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                '''
                INSERT INTO subscribers (
                    email,
                    token,
                    phone_number_sms,
                    agency_name,
                    counties_of_interest,
                    charge_types_of_interest,
                    subscription_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'ops@aaabailbonds.com',
                    'tok_2',
                    '4065552323',
                    'AAA Bail Bonds',
                    '["missoula"]',
                    '["assault"]',
                    'active',
                ),
            )
            conn.commit()
            conn.close()

            alerts = check_for_felony_bookings(
                [
                    {
                        'county_name': 'Cascade',
                        'person_name': 'Jane Roe',
                        'charges_summary': 'Felony Assault',
                    }
                ],
                db_path=path,
            )

            self.assertEqual(alerts, [])
        finally:
            os.remove(path)

    def test_skips_inactive_and_non_matching_charges(self) -> None:
        path = self._make_db()
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                '''
                INSERT INTO subscribers (
                    email,
                    token,
                    phone_number_sms,
                    agency_name,
                    counties_of_interest,
                    charge_types_of_interest,
                    subscription_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'inactive@example.com',
                    'tok_3',
                    '4065554545',
                    'Inactive Agency',
                    '["cascade"]',
                    '["dui_repeat"]',
                    'paused',
                ),
            )
            conn.commit()
            conn.close()

            alerts = check_for_felony_bookings(
                [
                    {
                        'county_name': 'Cascade',
                        'person_name': 'No Match',
                        'charges_summary': 'Driving without insurance',
                    }
                ],
                db_path=path,
            )

            self.assertEqual(alerts, [])
        finally:
            os.remove(path)

    def test_dispatch_logs_sent_sms_and_dedupes_replay(self) -> None:
        path = self._make_db()
        sent_messages: list[tuple[str, str]] = []
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                '''
                INSERT INTO subscribers (
                    email,
                    token,
                    phone_number_sms,
                    agency_name,
                    counties_of_interest,
                    charge_types_of_interest,
                    subscription_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'ops@aaabailbonds.com',
                    'tok_4',
                    '4065552323',
                    'AAA Bail Bonds',
                    '["missoula"]',
                    '["all"]',
                    'trialing',
                ),
            )

            def fake_send_sms(phone_number: str, sms_body: str) -> tuple[bool, str, str]:
                sent_messages.append((phone_number, sms_body))
                return True, 'SM123', ''

            payload = [
                {
                    'booking_id': 77,
                    'county_slug': 'missoula',
                    'county_name': 'Missoula',
                    'person_name': 'Jane Doe',
                    'booking_at': '2026-04-03 12:00:00',
                    'charges_summary': 'Felony Assault',
                }
            ]

            first = dispatch_felony_booking_alerts(conn, payload, send_sms=fake_send_sms)
            second = dispatch_felony_booking_alerts(conn, payload, send_sms=fake_send_sms)
            conn.commit()

            row = conn.execute(
                '''
                SELECT delivery_status, provider_message_id
                FROM bail_bonds_sms_deliveries
                WHERE subscriber_id = 1 AND booking_id = 77
                '''
            ).fetchone()
            conn.close()

            self.assertEqual(first['sent'], 1)
            self.assertEqual(second['skipped'], 1)
            self.assertEqual(len(sent_messages), 1)
            self.assertEqual(row['delivery_status'], 'sent')
            self.assertEqual(row['provider_message_id'], 'SM123')
        finally:
            os.remove(path)

    def test_ensure_schema_creates_telegram_deliveries_table(self) -> None:
        path = self._make_db()
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            ensure_bail_bonds_alert_schema(conn)
            conn.commit()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn('telegram_deliveries', tables)

            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info('telegram_deliveries')"
                ).fetchall()
            }
            for col in ('id', 'chat_id', 'booking_id', 'county_slug', 'message_text',
                        'delivery_status', 'telegram_message_id', 'error_message',
                        'created_at', 'delivered_at'):
                self.assertIn(col, cols)

            # Verify UNIQUE(chat_id, booking_id) is enforced
            conn.execute(
                "INSERT INTO telegram_deliveries (chat_id, booking_id) VALUES ('x', 1)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO telegram_deliveries (chat_id, booking_id) VALUES ('x', 1)"
                )

            conn.close()
        finally:
            os.remove(path)

    def test_get_telegram_chat_id_routes_by_county(self) -> None:
        import unittest.mock as mock
        with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', '-1001111111111'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', '-1002222222222'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', '-1003333333333'):
            from bail_bonds_alerts import get_telegram_chat_id
            self.assertEqual(get_telegram_chat_id('cascade'), '-1001111111111')
            self.assertEqual(get_telegram_chat_id('yellowstone'), '-1002222222222')
            self.assertEqual(get_telegram_chat_id('missoula'), '-1003333333333')
            self.assertEqual(get_telegram_chat_id('flathead'), '-1003333333333')

    def test_get_telegram_chat_id_returns_none_when_target_unset(self) -> None:
        import unittest.mock as mock
        with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', ''), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', ''), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', ''):
            from bail_bonds_alerts import get_telegram_chat_id
            self.assertIsNone(get_telegram_chat_id('cascade'))
            self.assertIsNone(get_telegram_chat_id('missoula'))

    def test_build_telegram_alert_contains_key_fields(self) -> None:
        from bail_bonds_alerts import build_telegram_alert
        booking = {
            'county_name': 'Cascade',
            'county_slug': 'cascade',
            'person_name': 'John Smith',
            'booking_at': '2026-04-16 14:32:00',
            'charges_summary': 'Felony Burglary',
        }
        matched_keywords = ['felony', 'burglary']
        text = build_telegram_alert(booking, matched_keywords)
        self.assertIn('Cascade', text)
        self.assertIn('John Smith', text)
        self.assertIn('felony', text)
        self.assertIn('burglary', text)
        self.assertIn('2026-04-16', text)
        self.assertIn('<b>', text)


if __name__ == '__main__':
    unittest.main()
