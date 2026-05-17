"""
engine/progress.py
==================
WHAT THIS DOES:
    Computes the progress score for a goal based on its UoM (Unit of Measurement) type.
    This is the core business logic from the BRD Section 2.2.

WHY IT'S A SEPARATE MODULE:
    - Score formula is complex (4 different types, each with edge cases)
    - Frontend previews the score live as employee types → same function reused
    - If formula changes, one place to fix

INTERVIEW EXPLANATION:
    "The progress score is computed, not stored, which means if the formula is ever
    corrected, historical data auto-updates. The actual and target values are what's
    persisted — score is derived from them."
"""

from datetime import date, datetime
from typing import Optional


def compute_score(
    uom_type: str,
    target: Optional[float],
    actual: Optional[float],
    target_date: Optional[str] = None,
    actual_date: Optional[str] = None,
) -> float:
    """
    Returns a score between 0.0 and 1.0 (display as percentage by multiplying by 100).

    Parameters:
        uom_type    : 'numeric_min' | 'numeric_max' | 'timeline' | 'zero'
        target      : planned target value (None for zero-based goals)
        actual      : what was actually achieved
        target_date : ISO date string "YYYY-MM-DD" (timeline goals only)
        actual_date : ISO date string "YYYY-MM-DD" (timeline goals only)

    Returns:
        float: 0.0 to 1.0 (capped at 1.0 — no score above 100%)
    """

    # ----------------------------------------------------------------
    # Guard: if no actual data yet, score is 0
    # ----------------------------------------------------------------
    if actual is None:
        return 0.0

    # ================================================================
    # NUMERIC MIN — Higher achievement is better
    # Example: Sales Revenue (target ₹50L, actual ₹42L → 84%)
    # Formula: actual ÷ target
    # ================================================================
    if uom_type == "numeric_min":
        if not target or target == 0:
            return 0.0
        score = actual / target
        return min(score, 1.0)  # cap at 100% — over-achievement doesn't score > 1

    # ================================================================
    # NUMERIC MAX — Lower achievement is better
    # Example: Customer complaint TAT (target 24hrs, actual 18hrs → 133% → capped 100%)
    # Example: Cost overrun (target ₹10L, actual ₹12L → 83%)
    # Formula: target ÷ actual
    # ================================================================
    elif uom_type == "numeric_max":
        if not actual or actual == 0:
            return 1.0  # if actual is 0, you perfectly minimised it
        if not target or target == 0:
            return 0.0
        score = target / actual
        return min(score, 1.0)

    # ================================================================
    # TIMELINE — Was the task completed before the deadline?
    # Example: CRM migration deadline Sep 30, completed Sep 15 → 100%
    # Example: completed Oct 10 (10 days late on 30-day month) → partial
    # Formula: if on time → 1.0, else compute how late vs window
    # ================================================================
    elif uom_type == "timeline":
        if not target_date or not actual_date:
            return 0.0
        deadline = _parse_date(target_date)
        completed = _parse_date(actual_date)

        if completed <= deadline:
            return 1.0  # on time or early — full score

        # Late: score decreases based on how many days late
        # We use a 30-day "grace window" — after 30 days late, score = 0
        days_late = (completed - deadline).days
        score = max(0.0, 1.0 - (days_late / 30))
        return round(score, 4)

    # ================================================================
    # ZERO-BASED — Success only if actual = 0
    # Example: Safety incidents (0 = 100%, any incident = 0%)
    # Example: Customer escalations to CEO (0 = success)
    # Formula: if actual == 0 → 1.0 else 0.0
    # ================================================================
    elif uom_type == "zero":
        return 1.0 if (actual == 0) else 0.0

    else:
        raise ValueError(f"Unknown UoM type: {uom_type}")


def _parse_date(date_str: str) -> date:
    """Parse ISO date string to date object."""
    return datetime.strptime(date_str, "%Y-%m-%d").date()


# ----------------------------------------------------------------
# WEIGHTED OVERALL SCORE for an employee's full goal sheet
# ----------------------------------------------------------------
def compute_overall_score(goals_with_scores: list[dict]) -> float:
    """
    Compute the weighted average score across all goals.

    Each dict must have: {'weightage': float, 'score': float}
    weightage values should sum to 100.

    Example:
        Goal A: weightage=40, score=0.84  → contributes 40*0.84 = 33.6
        Goal B: weightage=30, score=1.00  → contributes 30*1.00 = 30.0
        Goal C: weightage=30, score=1.00  → contributes 30*1.00 = 30.0
        Total = 93.6 / 100 = 0.936 = 93.6%

    INTERVIEW NOTE: "This is a tracking score, not a rating.
    The BRD explicitly says this is for progress visibility only,
    not for appraisal rating computation."
    """
    total_weight = sum(g["weightage"] for g in goals_with_scores)
    if total_weight == 0:
        return 0.0

    weighted_sum = sum(g["weightage"] * g["score"] for g in goals_with_scores)
    return round(weighted_sum / total_weight, 4)


# ================================================================
# SELF-TEST — run: python engine/progress.py
# ================================================================
if __name__ == "__main__":
    print("=" * 55)
    print("GoalQuest — Progress Score Engine Self-Test")
    print("=" * 55)

    tests = [
        # (label, uom_type, target, actual, target_date, actual_date, expected_approx)
        ("Sales ₹50L, achieved ₹42L",         "numeric_min", 50, 42,   None, None,          0.84),
        ("Sales ₹50L, achieved ₹60L (over)",   "numeric_min", 50, 60,   None, None,          1.00),
        ("TAT 24hr, actual 18hr (lower=good)", "numeric_max", 24, 18,   None, None,          1.00),
        ("TAT 24hr, actual 30hr (over budget)","numeric_max", 24, 30,   None, None,          0.80),
        ("Timeline: on time",                  "timeline",    None, 1,    "2025-09-30", "2025-09-20", 1.00),
        ("Timeline: 10 days late",             "timeline",    None, 1,    "2025-09-30", "2025-10-10", 0.67),
        ("Timeline: 35 days late (→ 0)",       "timeline",    None, 1,    "2025-09-30", "2025-11-04", 0.00),
        ("Zero-based: 0 incidents ✅",          "zero",        None, 0,   None, None,          1.00),
        ("Zero-based: 2 incidents ❌",          "zero",        None, 2,   None, None,          0.00),
    ]

    all_passed = True
    for label, uom, target, actual, tdate, adate, expected in tests:
        result = compute_score(uom, target, actual, tdate, adate)
        status = "✅" if abs(result - expected) < 0.02 else "❌"
        if status == "❌":
            all_passed = False
        print(f"  {status}  {label}")
        print(f"       Score: {result:.4f}  (expected ~{expected})")

    print()
    print("Overall Score Test:")
    goals = [
        {"weightage": 40, "score": 0.84},
        {"weightage": 30, "score": 1.00},
        {"weightage": 30, "score": 1.00},
    ]
    overall = compute_overall_score(goals)
    print(f"  Weighted score: {overall:.4f} = {overall*100:.1f}%  (expected ~93.6%)")
    print()
    print("All tests passed ✅" if all_passed else "Some tests FAILED ❌")
