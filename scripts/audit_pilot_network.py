"""Run automated network feasibility audit for candidate pilot monitoring network.

Audits candidate stations for sensor coverage, temporal completeness, gap rates,
consecutive outage intervals, and spatial overlap across a continuous multi-month
historical window.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from sensorguard.audit import audit_network_feasibility
from sensorguard.providers import OpenAQClient

# 8 verified candidate reference monitoring stations across UK AURN Network
# (Greater London cluster + South East / Central England reference stations)
PILOT_LOCATION_IDS = [141, 153, 154, 157, 698, 804, 1795, 2312]
DEFAULT_WINDOW_FROM = "2024-01-01T00:00:00Z"
DEFAULT_WINDOW_TO = "2024-03-31T23:59:59Z"


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Audit candidate air quality monitoring network")
    parser.add_argument(
        "--from",
        dest="datetime_from",
        default=DEFAULT_WINDOW_FROM,
        help="UTC start timestamp (ISO 8601)",
    )
    parser.add_argument(
        "--to",
        dest="datetime_to",
        default=DEFAULT_WINDOW_TO,
        help="UTC end timestamp (ISO 8601)",
    )
    parser.add_argument(
        "--output",
        default="docs/audit/pilot_network_audit.json",
        help="Path for saving machine-readable JSON audit report",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum pages to fetch per sensor (at 1000 records per page)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Number of records per page (up to 1000)",
    )
    args = parser.parse_args()

    client = OpenAQClient()

    print(f"Auditing network across {len(PILOT_LOCATION_IDS)} candidate stations...")
    print(f"Time window: {args.datetime_from} -> {args.datetime_to}")

    report = audit_network_feasibility(
        location_ids=PILOT_LOCATION_IDS,
        client=client,
        datetime_from=args.datetime_from,
        datetime_to=args.datetime_to,
        network_name="UK AURN Greater London & South East Network",
        country="GB",
        bounding_box=(-0.85, 50.80, 1.60, 52.35),
        min_eligible_stations=8,
        max_gap_rate=0.40,
        min_overlap_4plus_pct=0.60,
        max_pages=args.max_pages,
        page_size=args.page_size,
    )

    report_dict = report.to_dict()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report_dict, indent=2, default=str), encoding="utf-8")

    print("\n================ AUDIT SUMMARY ================")
    print(f"Decision:               {report.selection_decision}")
    print(f"Eligible Stations:      {report.eligible_stations_count}/{report.total_audited_stations}")
    print(f"Expected Hours:         {report.total_expected_hours}")
    print(f"Overlap (>=4 stations): {report.overlap_pct_4plus:.1%}")
    print(f"Overlap (all stations): {report.overlap_hours_all} hours")
    print(f"Rationale:              {report.decision_rationale}")
    print(f"Saved Report:           {out_path}")
    print("================================================")

    return 0 if report.selection_decision == "APPROVED" else 1


if __name__ == "__main__":
    sys.exit(main())
