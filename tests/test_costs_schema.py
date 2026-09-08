import csv

EXPECTED_FIELDNAMES = [
    "session_id",
    "start",
    "end",
    "cost_estimate_usd",
    "cost_actual_usd",
    "cost_status",
    "ce_query_date",
]


def test_sessions_csv_has_documented_schema() -> None:
    with open("costs/sessions.csv", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == EXPECTED_FIELDNAMES
        for row in reader:
            assert row["cost_status"] in ("pending", "final", "reaper-terminated", "")
