"""
engine/cycle.py
===============
WHAT THIS DOES:
    Determines which quarter is currently active based on the
    cycle configuration set by Admin.

WHY THIS MATTERS:
    - Employees can only log achievements during the open quarter window
    - Goal creation is only allowed during GOAL_SETTING phase
    - Prevents backdating or future-dating entries

INTERVIEW EXPLANATION:
    "The Admin controls all cycle dates. The system computes the current
    quarter at runtime — no hardcoding. This means the same codebase works
    across financial years with just a DB config change."
"""

from datetime import date, datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class Cycle:
    """Mirrors the cycles table in the DB."""
    id: str
    name: str
    goal_setting_opens: str   # ISO date
    q1_opens: str
    q2_opens: str
    q3_opens: str
    q4_opens: str


# The phases in order — used for both display and logic
PHASES = ["GOAL_SETTING", "Q1", "Q2", "Q3", "Q4"]


def get_current_phase(cycle: Cycle, today: Optional[date] = None) -> Optional[str]:
    """
    Returns the current active phase based on today's date.

    Logic: Compare today against the cascade of window-open dates.
    The phase is whichever window most recently opened.

    Returns None if today is before the cycle even starts.
    """
    today = today or date.today()

    def to_date(s: str) -> date:
        return datetime.strptime(s, "%Y-%m-%d").date()

    gs  = to_date(cycle.goal_setting_opens)
    q1  = to_date(cycle.q1_opens)
    q2  = to_date(cycle.q2_opens)
    q3  = to_date(cycle.q3_opens)
    q4  = to_date(cycle.q4_opens)

    if today < gs:
        return None              # Cycle hasn't started yet
    elif today < q1:
        return "GOAL_SETTING"
    elif today < q2:
        return "Q1"
    elif today < q3:
        return "Q2"
    elif today < q4:
        return "Q3"
    else:
        return "Q4"


def is_goal_setting_open(cycle: Cycle, today: Optional[date] = None) -> bool:
    """Can employees create and submit goals?"""
    return get_current_phase(cycle, today) == "GOAL_SETTING"


def is_checkin_open(cycle: Cycle, quarter: str, today: Optional[date] = None) -> bool:
    """Can employees log achievements for this quarter?"""
    return get_current_phase(cycle, today) == quarter


def days_until_deadline(cycle: Cycle, phase: str, today: Optional[date] = None) -> int:
    """
    How many days until the current phase ends (next phase opens)?
    Used by the escalation engine to determine urgency.
    """
    today = today or date.today()

    def to_date(s: str) -> date:
        return datetime.strptime(s, "%Y-%m-%d").date()

    phase_ends = {
        "GOAL_SETTING": to_date(cycle.q1_opens),
        "Q1":           to_date(cycle.q2_opens),
        "Q2":           to_date(cycle.q3_opens),
        "Q3":           to_date(cycle.q4_opens),
        "Q4":           to_date(cycle.q4_opens),   # Q4 end = same date for now
    }

    if phase not in phase_ends:
        return 0
    return max(0, (phase_ends[phase] - today).days)


# ================================================================
# SELF-TEST — run: python engine/cycle.py
# ================================================================
if __name__ == "__main__":
    demo_cycle = Cycle(
        id="cycle-2526",
        name="FY2025-26",
        goal_setting_opens="2025-05-01",
        q1_opens="2025-07-01",
        q2_opens="2025-10-01",
        q3_opens="2026-01-01",
        q4_opens="2026-03-01",
    )

    test_dates = [
        ("2025-04-15", None),              # before cycle
        ("2025-05-15", "GOAL_SETTING"),    # goal setting window
        ("2025-07-15", "Q1"),              # Q1 check-in window
        ("2025-10-20", "Q2"),              # Q2 check-in window
        ("2026-01-10", "Q3"),              # Q3 check-in window
        ("2026-03-20", "Q4"),              # Q4 / annual
    ]

    print("Cycle Engine Self-Test")
    print("=" * 45)
    all_passed = True
    for date_str, expected in test_dates:
        test_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        result = get_current_phase(demo_cycle, today=test_date)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_passed = False
        print(f"  {status}  {date_str}  →  {result}  (expected {expected})")

    print()
    print(f"is_goal_setting_open on 2025-06-01:",
          is_goal_setting_open(demo_cycle, datetime.strptime("2025-06-01", "%Y-%m-%d").date()))
    print(f"is_checkin_open(Q1) on 2025-08-01:",
          is_checkin_open(demo_cycle, "Q1", datetime.strptime("2025-08-01", "%Y-%m-%d").date()))
    print()
    print("All tests passed ✅" if all_passed else "Some tests FAILED ❌")
