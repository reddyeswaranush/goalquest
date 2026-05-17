"""
engine/escalation.py
====================
WHAT THIS DOES:
    Checks all active escalation rules against the current DB state
    and fires notifications when thresholds are breached.

INTERVIEW EXPLANATION:
    "The escalation engine runs on a schedule — daily in production via
    a cron job or Supabase Edge Function. It's rule-based: Admin configures
    which events trigger escalations and after how many days. The engine
    evaluates each rule against current data and notifies the right person.

    We made it configurable (not hardcoded) so HR can adjust thresholds
    without a code deployment."

HOW IT WORKS:
    1. Load all active escalation rules from DB
    2. For each rule, run a query to find violating records
    3. Log the escalation event (notify in prod, print in demo)
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional


def run_escalation_check(
    conn: sqlite3.Connection,
    cycle_id: str,
    today: Optional[date] = None,
) -> list[dict]:
    """
    Run all active escalation rules for the given cycle.

    Returns:
        List of escalation events (dicts) that were triggered.
        In production: send emails here. In demo: just return/print them.
    """
    today = today or date.today()
    events = []

    rules = conn.execute(
        "SELECT id, trigger_event, days_threshold, notify_level FROM escalation_rules WHERE is_active = 1"
    ).fetchall()

    for rule_id, trigger, days, notify_level in rules:

        # ----------------------------------------------------------
        # RULE: goal_not_submitted
        # Find employees who haven't submitted any goals N days after
        # the goal setting window opened.
        # ----------------------------------------------------------
        if trigger == "goal_not_submitted":
            cycle = conn.execute(
                "SELECT goal_setting_opens FROM cycles WHERE id = ?", (cycle_id,)
            ).fetchone()
            if not cycle:
                continue

            window_open = datetime.strptime(cycle[0], "%Y-%m-%d").date()
            threshold_date = window_open + timedelta(days=days)

            if today < threshold_date:
                continue  # Not yet time to escalate

            # Find employees with NO submitted/approved/locked goals in this cycle
            laggards = conn.execute(
                """
                SELECT DISTINCT p.id, p.name, p.email, p.manager_id
                FROM profiles p
                WHERE p.role = 'employee'
                AND p.id NOT IN (
                    SELECT DISTINCT employee_id FROM goals
                    WHERE cycle_id = ? AND status IN ('submitted','approved','locked')
                )
                """,
                (cycle_id,),
            ).fetchall()

            for emp_id, emp_name, emp_email, manager_id in laggards:
                events.append({
                    "rule_id":      rule_id,
                    "trigger":      trigger,
                    "days_overdue": (today - threshold_date).days,
                    "notify_level": notify_level,
                    "notify_who":   _notify_target(notify_level, emp_id, manager_id),
                    "employee_id":  emp_id,
                    "employee_name": emp_name,
                    "message":      f"{emp_name} has not submitted goals {days}+ days after cycle opened.",
                })

        # ----------------------------------------------------------
        # RULE: goal_not_approved
        # Find goals submitted N+ days ago that managers haven't approved.
        # ----------------------------------------------------------
        elif trigger == "goal_not_approved":
            pending = conn.execute(
                """
                SELECT g.id, g.employee_id, p.name, p.manager_id, g.updated_at
                FROM goals g
                JOIN profiles p ON g.employee_id = p.id
                WHERE g.cycle_id = ? AND g.status = 'submitted'
                """,
                (cycle_id,),
            ).fetchall()

            for goal_id, emp_id, emp_name, manager_id, submitted_at in pending:
                submitted_date = datetime.strptime(submitted_at[:10], "%Y-%m-%d").date()
                if (today - submitted_date).days >= days:
                    events.append({
                        "rule_id":      rule_id,
                        "trigger":      trigger,
                        "days_overdue": (today - submitted_date).days - days,
                        "notify_level": notify_level,
                        "notify_who":   _notify_target(notify_level, emp_id, manager_id),
                        "employee_id":  emp_id,
                        "employee_name": emp_name,
                        "message":      f"Goals submitted by {emp_name} pending manager approval for {(today-submitted_date).days} days.",
                    })

        # ----------------------------------------------------------
        # RULE: checkin_not_done
        # Find locked goals with no achievement entry for current quarter.
        # ----------------------------------------------------------
        elif trigger == "checkin_not_done":
            cycle_row = conn.execute(
                "SELECT q1_opens, q2_opens, q3_opens, q4_opens FROM cycles WHERE id = ?",
                (cycle_id,)
            ).fetchone()
            if not cycle_row:
                continue

            # Determine current quarter and when it opened
            quarters = {
                "Q1": datetime.strptime(cycle_row[0], "%Y-%m-%d").date(),
                "Q2": datetime.strptime(cycle_row[1], "%Y-%m-%d").date(),
                "Q3": datetime.strptime(cycle_row[2], "%Y-%m-%d").date(),
                "Q4": datetime.strptime(cycle_row[3], "%Y-%m-%d").date(),
            }
            current_q = None
            current_open = None
            for q, open_date in sorted(quarters.items(), key=lambda x: x[1], reverse=True):
                if today >= open_date:
                    current_q = q
                    current_open = open_date
                    break

            if not current_q:
                continue

            threshold_date = current_open + timedelta(days=days)
            if today < threshold_date:
                continue

            # Find employees with locked goals but no achievement for current quarter
            missing = conn.execute(
                """
                SELECT DISTINCT p.id, p.name, p.manager_id
                FROM profiles p
                JOIN goals g ON g.employee_id = p.id
                WHERE g.cycle_id = ? AND g.status = 'locked'
                AND g.id NOT IN (
                    SELECT goal_id FROM achievements WHERE quarter = ?
                )
                """,
                (cycle_id, current_q),
            ).fetchall()

            for emp_id, emp_name, manager_id in missing:
                events.append({
                    "rule_id":      rule_id,
                    "trigger":      trigger,
                    "quarter":      current_q,
                    "notify_level": notify_level,
                    "notify_who":   _notify_target(notify_level, emp_id, manager_id),
                    "employee_id":  emp_id,
                    "employee_name": emp_name,
                    "message":      f"{emp_name} has not completed {current_q} check-in ({days}+ days into quarter).",
                })

    return events


def _notify_target(level: int, employee_id: str, manager_id: Optional[str]) -> str:
    """Map notify_level to who gets notified."""
    if level == 1:
        return employee_id          # Notify the employee themselves
    elif level == 2:
        return manager_id or "HR"   # Notify their manager
    else:
        return "HR"                 # Notify HR / skip-level


# ================================================================
# SELF-TEST — run: python engine/escalation.py
# ================================================================
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY, name TEXT, email TEXT,
            role TEXT, manager_id TEXT, department TEXT, created_at TEXT
        );
        CREATE TABLE cycles (
            id TEXT PRIMARY KEY, name TEXT,
            goal_setting_opens TEXT, q1_opens TEXT, q2_opens TEXT,
            q3_opens TEXT, q4_opens TEXT, is_active INTEGER
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, employee_id TEXT, cycle_id TEXT,
            thrust_area TEXT, title TEXT, description TEXT,
            uom_type TEXT, target REAL, target_date TEXT,
            weightage REAL, status TEXT, is_shared INTEGER DEFAULT 0,
            shared_parent_id TEXT, return_reason TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE achievements (
            id TEXT PRIMARY KEY, goal_id TEXT, quarter TEXT,
            actual REAL, actual_date TEXT, progress_status TEXT,
            computed_score REAL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE escalation_rules (
            id TEXT PRIMARY KEY, trigger_event TEXT,
            days_threshold INTEGER, notify_level INTEGER, is_active INTEGER
        );

        -- Users
        INSERT INTO profiles VALUES
            ('emp-001','Anita Nair','anita@co.com','employee','mgr-001','Sales','2025-05-01'),
            ('emp-002','Dev Patel','dev@co.com','employee','mgr-001','Sales','2025-05-01'),
            ('mgr-001','Raj Mehta','raj@co.com','manager',NULL,'Sales','2025-05-01');

        -- Cycle: goal setting opened 30 days ago
        INSERT INTO cycles VALUES
            ('c1','FY2025-26','2025-04-01','2025-07-01','2025-10-01','2026-01-01','2026-03-01',1);

        -- Anita has submitted, Dev has not
        INSERT INTO goals VALUES
            ('g1','emp-001','c1','Rev','Target','Desc','numeric_min',50,NULL,40,'submitted',
             0,NULL,NULL,'2025-05-01','2025-05-05');

        -- Rules
        INSERT INTO escalation_rules VALUES
            ('r1','goal_not_submitted',7,1,1),
            ('r2','goal_not_submitted',14,2,1),
            ('r3','goal_not_approved',5,2,1);
    """)

    today = datetime.strptime("2025-05-01", "%Y-%m-%d").date()
    events = run_escalation_check(conn, "c1", today=today)
    print("Escalation Engine Self-Test")
    print("=" * 50)
    print(f"Events triggered: {len(events)}")
    for e in events:
        print(f"\n  Trigger : {e['trigger']}")
        print(f"  Employee: {e['employee_name']}")
        print(f"  Level   : {e['notify_level']} → notify {e['notify_who']}")
        print(f"  Message : {e['message']}")
    print()
    print("✅ Escalation engine working" if events else "⚠️  No events (check dates)")
