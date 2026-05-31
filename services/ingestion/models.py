from dataclasses import dataclass


@dataclass(frozen=True)
class JailBookingRecord:
    source_record_id: str
    person_name: str
    age: int | None
    booking_number: str
    booking_at: str | None
    charges_summary: str
    source_url: str | None = None
