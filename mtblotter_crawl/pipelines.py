import sqlite3

class SQLitePipeline:
    def __init__(self, db_path):
        self.db_path = db_path

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get("MTBLOTTER_DB_PATH"))

    def open_spider(self, spider):
        self.conn = sqlite3.connect(self.db_path)
        self.cur = self.conn.cursor()

    def close_spider(self, spider):
        self.conn.commit()
        self.conn.close()

    def process_item(self, item, spider):
        try:
            self.cur.execute(
                """INSERT OR IGNORE INTO jail_bookings
                   (county_slug, county_name, facility_name, person_name,
                    booking_at, charges_summary, source_url, booking_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    item.get("county_slug", str(item.get("county","")).lower().replace(" ", "_")),
                    item.get("county", ""),
                    item.get("facility", str(item.get("county","")) + " County Detention Center"),
                    item.get("inmate_name", ""),
                    item.get("booking_date"),
                    item.get("charges", ""),
                    item.get("source_url"),
                    "current",
                ),
            )
            self.conn.commit()
        except Exception as e:
            spider.logger.warning("SQLite insert failed: %s", e)
        return item
