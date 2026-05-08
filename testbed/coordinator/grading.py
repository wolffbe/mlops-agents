"""Grade a run's submission.csv using MLE-bench's official grader.

Returns the full grading report as a dict (score + medal tiers + validity)
so the coordinator can persist all of it to results.csv.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / ".local" / "mle-bench-data"

EMPTY_REPORT_FIELDS = {
    "score": None,
    "valid_submission": False,
    "submission_exists": False,
    "any_medal": False,
    "gold_medal": False,
    "silver_medal": False,
    "bronze_medal": False,
    "above_median": False,
}


def grade_run(task: str, workspace: Path) -> dict:
    submission = workspace / "submission.csv"
    report = dict(EMPTY_REPORT_FIELDS)
    report["submission_exists"] = submission.exists() and submission.stat().st_size > 0

    if not report["submission_exists"]:
        print(f"[grading] no submission.csv at {submission}")
        return report

    data_dir = Path(os.environ.get("MLE_BENCH_DATA_DIR", DEFAULT_DATA_DIR))

    try:
        from mlebench.grade import grade_csv
        from mlebench.registry import registry as default_registry

        registry = default_registry.set_data_dir(data_dir)
        competition = registry.get_competition(task)
        cr = grade_csv(submission, competition)
        full = cr.to_dict() if hasattr(cr, "to_dict") else dict(cr.__dict__)
        # Keep the keys we care about; drop any non-CSV-friendly extras.
        for k in EMPTY_REPORT_FIELDS:
            if k in full:
                report[k] = full[k]
        return report
    except Exception as e:
        print(f"[grading] mlebench grade_csv failed: {e}")
        return report
