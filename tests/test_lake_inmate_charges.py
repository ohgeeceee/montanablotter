"""Regression tests for Lake County roster charge-block assignment.

Bug report (2026-09-23): the public record for Marissa Grant Basler
(booking 26-015219, Lake County, booked 2026-09-20) showed
"45-5-502(3) - Sexual Assault ... Less than 16" — the charge block of the
NEXT inmate on the roster PDF — while her actual charge was an
aggravated DUI (61-8-1002(1)(a)).

Root cause: Lake County's ReportLab PDF vertically centers each inmate's
multi-line charge block against their booking row, so the block's earlier
lines sit ABOVE the header row (in the gap after the previous inmate).
The old rule ("assign each fragment to the last header >= 8pt above it")
handed those first lines to the previous inmate — a systematic
off-by-one shift down the column.

Fixed rule under test (lake_inmate._assign_charges): each fragment goes to
the FIRST booking row whose bottom edge reaches down to it; continuation
lines (no statute prefix) follow their parent line's target; page-tail
statute blocks carry over to the next page's first inmate.
"""
import unittest

from services.ingestion.fetchers import lake_inmate as li


def _row(top, bottom, jacket="26-000001"):
    """A booking row tuple as produced by _parse_page: (top, bottom, fields)."""
    return (top, bottom, {"jacket": jacket, "person_name": jacket})


class TestAssignCharges(unittest.TestCase):
    # Two inmate headers; each row's bottom edge sits at its own baseline.
    ROWS = [_row(100.0, 109.0, "26-000001"), _row(160.0, 169.0, "26-000002")]

    def test_block_above_header_not_shifted_to_previous_inmate(self):
        """The exact Basler bug: inmate 2's multi-line block starts above
        inmate 2's header row (in the gap after inmate 1) — every line must
        land on inmate 2, never on inmate 1."""
        fragments = [
            (112.0, "45-5-625(2) - Sexual Abuse of Children Under 16"),
            (120.0, "in Subsection 45-9-102(1) or (2)"),
            (128.0, "45-3-1102 - Negligent Assault"),
            (164.0, "45-6-315 - Failure to Appear"),
        ]
        charges, carry = li._assign_charges(self.ROWS, fragments)
        self.assertEqual([], charges[0])
        self.assertEqual(4, len(charges[1]))
        self.assertIn("Sexual Abuse of Children Under 16", charges[1][0])
        self.assertIn("in Subsection", charges[1][1])
        self.assertIn("Negligent Assault", charges[1][2])
        self.assertIn("Failure to Appear", charges[1][3])
        self.assertEqual([], carry)

    def test_continuation_follows_parent_not_geometry(self):
        """A wrapped continuation line (Bybee case: 'in Subsection ...')
        stays glued to its parent line's inmate even if it drifts across
        the anchor boundary — it must never start a new charge for the
        next inmate."""
        fragments = [
            (104.0, "45-9-102 - Criminal Possession"),     # inmate 1's block
            (161.5, "in Subsection 45-9-102(1) or (2)"),   # wraps far below its parent
        ]
        charges, carry = li._assign_charges(self.ROWS, fragments)
        self.assertEqual(2, len(charges[0]))
        self.assertIn("Criminal Possession", charges[0][0])
        self.assertIn("in Subsection", charges[0][1])
        self.assertEqual([], charges[1])
        self.assertEqual([], carry)
        # and the joiner merges them into one readable charge
        joined = li._join_charge_parts(charges[0])
        self.assertEqual(
            "45-9-102 - Criminal Possession in Subsection 45-9-102(1) or (2)",
            joined,
        )

    def test_new_statute_block_below_last_header_carries_to_next_page(self):
        """A fragment starting a NEW charge block below the last header on
        the page is the first line of the next page's first inmate."""
        fragments = [
            (104.0, "45-6-311 - Resisting Arrest"),
            (500.0, "61-8-1002(1)(a) - Aggravated DUI"),  # page tail, new block
        ]
        charges, carry = li._assign_charges(self.ROWS, fragments)
        self.assertIn("Resisting Arrest", charges[0][0])
        self.assertEqual(1, len(carry))
        self.assertIn("Aggravated DUI", carry[0])

    def test_page_tail_continuation_follows_parent(self):
        """A non-statute fragment at the page tail continues its PARENT
        line's charge (not the last inmate's), and must not leak into
        carry_out where it could be re-attached on the next page."""
        fragments = [
            (104.0, "45-6-311 - Resisting Arrest"),
            (500.0, "Causing Bodily Injury"),
        ]
        charges, carry = li._assign_charges(self.ROWS, fragments)
        self.assertEqual([], carry)
        self.assertEqual(2, len(charges[0]))
        self.assertIn("Causing Bodily Injury", charges[0][1])
        self.assertEqual([], charges[1])

    def test_carry_in_attaches_to_first_inmate(self):
        """Previous page's tail fragments land on this page's first header."""
        fragments = [(164.0, "47-1-100 - Reckless Offense")]
        charges, carry = li._assign_charges(
            self.ROWS, fragments, carry_in=["45-5-503(1) - Sexual Intercourse"]
        )
        self.assertIn("Sexual Intercourse", charges[0][0])
        self.assertEqual(1, len(charges[0]))
        self.assertIn("Reckless Offense", charges[1][0])
        self.assertEqual([], carry)

    def test_fragment_within_baseline_tolerance_uses_own_row(self):
        """A fragment up to _BASELINE_TOL below its row's bottom edge still
        belongs to that row, not the next one."""
        fragments = [(self.ROWS[0][1] + li._BASELINE_TOL - 0.5, "45-6-301 - Theft")]
        charges, carry = li._assign_charges(self.ROWS, fragments)
        self.assertIn("Theft", charges[0][0])
        self.assertEqual([], charges[1])


class TestJoinChargeParts(unittest.TestCase):
    def test_continuations_merge_with_space(self):
        parts = [
            "45-9-102 - Criminal Possession",
            "in Subsection 45-9-102(1) or (2)",
            "45-7-308(4) - Bail Jumping",
        ]
        self.assertEqual(
            "45-9-102 - Criminal Possession in Subsection 45-9-102(1) or (2); "
            "45-7-308(4) - Bail Jumping",
            li._join_charge_parts(parts),
        )

    def test_statute_start_never_merges(self):
        parts = ["45-5-104 - Negligent Homicide", "45-5-207 - Endangerment"]
        self.assertEqual("; ".join(parts), li._join_charge_parts(parts))


class TestParseBookingRow(unittest.TestCase):
    def test_standard_roster_line(self):
        parsed = li._parse_booking_row(
            "BASLER, MARISSA GRANT 26-015219 38 W Female 3 09/20/26 SHERIFF"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual("26-015219", parsed["jacket"])
        self.assertEqual("Basler, Marissa Grant", parsed["person_name"])
        self.assertEqual("09/20/26", parsed["booking_date"])

    def test_malformed_line_returns_none(self):
        self.assertIsNone(li._parse_booking_row("Page 2 of 3"))


if __name__ == "__main__":
    unittest.main()
