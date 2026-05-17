"""
engine/audit.py
===============
WHAT THIS DOES:
    Provides a single function `log()` that every mutation in the system
    calls to record what changed, who changed it, and when.

WHY AUDIT TRAILS MATTER (interview answer):
    "An audit trail is required for SOC-2 compliance and HR accountability.
    In enterprise HR tools, managers sometimes edit targets before approving —
    this must be traceable. If an employee disputes their appraisal, HR can
    replay exactly what was changed and by whom.

    We made audit logs append-only (never delete/update audit rows) so they
    can serve as a legal record."

DESIGN DECISION: We store old_value and new_value as JSON strings.
    This gives us a human-readable diff without needing a separate diff library.
"""

import json
import sqlite3
from typing import Any, Optional
from datetime import datetime


def log(
    conn: sqlite3.Connection,
    actor_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
) -> None:
    """
    Insert one immutable audit log entry.

    Parameters:
        conn        : active SQLite connection
        actor_id    : profile ID of the person making the change
        entity_type : 'goal' | 'achievement' | 'checkin' | 'cycle' | 'user'
        entity_id   : ID of the record being changed
        action      : verb describing what happened
                      'created' | 'submitted' | 'approved' | 'returned' |
                      'edited' | 'locked' | 'unlocked' | 'deleted' | 'synced'
        old_value   : dict of fields BEFORE the change (None for creates)
        new_value   : dict of fields AFTER the change (None for deletes)
    """
    conn.execute(
        """
        INSERT INTO audit_logs
            (actor_id, entity_type, entity_id, action, old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id,
            entity_type,
            entity_id,
            action,
            json.dumps(old_value) if old_value is not None else None,
            json.dumps(new_value) if new_value is not None else None,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


def fetch_trail(conn: sqlite3.Connection, entity_id: str) -> list[dict]:
    """
    Fetch the full audit trail for a specific entity (goal, achievement, etc.)
    Returns list of events ordered oldest → newest.

    Used by:
    - Goal detail page sidebar (shows timeline of changes)
    - Admin audit log viewer (filtered by date range / actor)
    """
    rows = conn.execute(
        """
        SELECT
            al.id,
            p.name      AS actor_name,
            al.action,
            al.old_value,
            al.new_value,
            al.timestamp
        FROM audit_logs al
        LEFT JOIN profiles p ON al.actor_id = p.id
        WHERE al.entity_id = ?
        ORDER BY al.timestamp ASC
        """,
        (entity_id,),
    ).fetchall()

    return [
        {
            "id":         row[0],
            "actor":      row[1],
            "action":     row[2],
            "old_value":  json.loads(row[3]) if row[3] else None,
            "new_value":  json.loads(row[4]) if row[4] else None,
            "timestamp":  row[5],
        }
        for row in rows
    ]


def fetch_all_logs(
    conn: sqlite3.Connection,
    actor_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[dict]:
    """
    Fetch audit logs with optional filters — used by Admin audit viewer.
    All filters are optional (None = no filter applied).
    """
    clauses = []
    params = []

    if actor_id:
        clauses.append("al.actor_id = ?")
        params.append(actor_id)
    if entity_type:
        clauses.append("al.entity_type = ?")
        params.append(entity_type)
    if from_date:
        clauses.append("al.timestamp >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("al.timestamp <= ?")
        params.append(to_date)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"""
        SELECT al.id, p.name, al.entity_type, al.entity_id,
               al.action, al.old_value, al.new_value, al.timestamp
        FROM audit_logs al
        LEFT JOIN profiles p ON al.actor_id = p.id
        {where}
        ORDER BY al.timestamp DESC
        LIMIT 500
        """,
        params,
    ).fetchall()

    return [
        {
            "id":          row[0],
            "actor":       row[1],
            "entity_type": row[2],
            "entity_id":   row[3],
            "action":      row[4],
            "old_value":   json.loads(row[5]) if row[5] else None,
            "new_value":   json.loads(row[6]) if row[6] else None,
            "timestamp":   row[7],
        }
        for row in rows
    ]


# ================================================================
# SELF-TEST — run: python engine/audit.py
# ================================================================
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    # Use an in-memory DB for the test
    conn = sqlite3.connect(":memory:")

    # Minimal schema for the test
    conn.executescript("""
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY, name TEXT, email TEXT,
            role TEXT, manager_id TEXT, department TEXT, created_at TEXT
        );
        CREATE TABLE audit_logs (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            actor_id TEXT, entity_type TEXT, entity_id TEXT,
            action TEXT, old_value TEXT, new_value TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO profiles VALUES
            ('emp-001','Anita Nair','anita@co.com','employee',NULL,'Sales',datetime('now')),
            ('mgr-001','Raj Mehta','raj@co.com','manager',NULL,'Sales',datetime('now'));
    """)

    # Simulate goal lifecycle
    log(conn, "emp-001", "goal", "goal-xyz", "created",
        old_value=None,
        new_value={"title": "Q1 Revenue", "weightage": 40, "status": "draft"})

    log(conn, "emp-001", "goal", "goal-xyz", "submitted",
        old_value={"status": "draft"},
        new_value={"status": "submitted"})

    log(conn, "mgr-001", "goal", "goal-xyz", "edited",
        old_value={"target": 4500000},
        new_value={"target": 5000000})

    log(conn, "mgr-001", "goal", "goal-xyz", "approved",
        old_value={"status": "submitted"},
        new_value={"status": "locked"})

    trail = fetch_trail(conn, "goal-xyz")
    print("Audit Trail for goal-xyz:")
    print("=" * 50)
    for entry in trail:
        print(f"  [{entry['timestamp']}] {entry['actor']} → {entry['action']}")
        if entry["old_value"] or entry["new_value"]:
            print(f"    Before: {entry['old_value']}")
            print(f"    After:  {entry['new_value']}")
    print()
    print(f"Total entries: {len(trail)}  (expected 4) {'✅' if len(trail)==4 else '❌'}")
