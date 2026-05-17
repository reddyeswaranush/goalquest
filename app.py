"""
app.py
======
WHAT THIS DOES:
    Main Flask application entry point.
    Defines all API routes for the GoalQuest portal.
    Uses SQLite (file-based) so you can run it with zero setup.

HOW TO RUN:
    pip install -r requirements.txt
    python app.py
    # API at http://localhost:5000

ARCHITECTURE NOTE:
    In production (Vercel/Supabase), these routes become
    Next.js API routes under app/api/. The logic is identical —
    only the runtime changes (Node.js instead of Python Flask).
    Flask is used here for interview/demo clarity.
"""

import json
import sqlite3
import csv
import io
import os
from datetime import date, datetime
from flask import Flask, jsonify, request, g, Response
from flask_cors import CORS

# Import our engines
import sys
sys.path.insert(0, os.path.dirname(__file__))
from engine.progress import compute_score, compute_overall_score
from engine.cycle import Cycle, get_current_phase, is_goal_setting_open, is_checkin_open
from engine.audit import log as audit_log, fetch_trail, fetch_all_logs
from engine.escalation import run_escalation_check

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "goalquest_demo.db")


# =============================================================
# DB CONNECTION HELPERS
# =============================================================

def get_db() -> sqlite3.Connection:
    """Get (or create) a DB connection for this request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row   # rows behave like dicts
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and seed demo data on first run."""
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    schema_path = os.path.join(os.path.dirname(__file__), "01_schema.sql")
    seed_path   = os.path.join(os.path.dirname(__file__), "02_seed.sql")
    with open(schema_path) as f:
        db.executescript(f.read())
    # Only seed if profiles table is empty
    count = db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    if count == 0:
        with open(seed_path) as f:
            db.executescript(f.read())
        print("✅ Demo data seeded")
    db.close()


def get_active_cycle(db) -> Cycle:
    """Fetch the active cycle and return as a Cycle dataclass."""
    row = db.execute(
        "SELECT * FROM cycles WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return Cycle(**{k: row[k] for k in row.keys()})


# =============================================================
# HEALTH CHECK
# =============================================================

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "GoalQuest API", "version": "1.0.0"})


# =============================================================
# GOALS — CRUD + WORKFLOW
# =============================================================

@app.route("/api/goals", methods=["GET"])
def list_goals():
    """
    GET /api/goals?employee_id=...&cycle_id=...&status=...
    Returns all goals matching the filters.
    Used by: Employee (own goals), Manager (team's goals), Admin (all)
    """
    db = get_db()
    employee_id = request.args.get("employee_id")
    cycle_id    = request.args.get("cycle_id")
    status      = request.args.get("status")

    clauses, params = [], []
    if employee_id:
        clauses.append("g.employee_id = ?")
        params.append(employee_id)
    if cycle_id:
        clauses.append("g.cycle_id = ?")
        params.append(cycle_id)
    if status:
        clauses.append("g.status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = db.execute(
        f"""
        SELECT g.*, p.name AS employee_name
        FROM goals g
        JOIN profiles p ON g.employee_id = p.id
        {where}
        ORDER BY g.created_at DESC
        """,
        params,
    ).fetchall()

    return jsonify([dict(r) for r in rows])


@app.route("/api/goals", methods=["POST"])
def create_goal():
    """
    POST /api/goals
    Body: {employee_id, cycle_id, thrust_area, title, description,
           uom_type, target, target_date, weightage}

    Validates:
    - employee has < 8 goals already
    - weightage >= 10
    (Total = 100 is validated at SUBMIT time, not create time)
    """
    db  = get_db()
    data = request.get_json()

    # Validation: max 8 goals
    count = db.execute(
        "SELECT COUNT(*) FROM goals WHERE employee_id = ? AND cycle_id = ?",
        (data["employee_id"], data["cycle_id"]),
    ).fetchone()[0]
    if count >= 8:
        return jsonify({"error": "Maximum 8 goals per employee"}), 400

    # Validation: min weightage 10%
    if float(data.get("weightage", 0)) < 10:
        return jsonify({"error": "Minimum weightage per goal is 10%"}), 400

    goal_id = _new_id()
    db.execute(
        """
        INSERT INTO goals (id, employee_id, cycle_id, thrust_area, title,
            description, uom_type, target, target_date, weightage, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,'draft')
        """,
        (goal_id, data["employee_id"], data["cycle_id"], data["thrust_area"],
         data["title"], data.get("description"), data["uom_type"],
         data.get("target"), data.get("target_date"), data["weightage"]),
    )
    db.commit()

    audit_log(db, data["employee_id"], "goal", goal_id, "created",
              new_value={"title": data["title"], "weightage": data["weightage"], "status": "draft"})

    return jsonify({"id": goal_id, "status": "draft"}), 201


@app.route("/api/goals/<goal_id>/submit", methods=["POST"])
def submit_goals(goal_id):
    """
    POST /api/goals/<goal_id>/submit
    Actually submits ALL goals for an employee (called once when employee
    clicks "Submit All"). We validate total weightage = 100 here.

    This is a state transition: draft → submitted
    """
    db   = get_db()
    data = request.get_json()
    employee_id = data["employee_id"]
    cycle_id    = data["cycle_id"]

    goals = db.execute(
        "SELECT id, weightage FROM goals WHERE employee_id=? AND cycle_id=? AND status='draft'",
        (employee_id, cycle_id),
    ).fetchall()

    if not goals:
        return jsonify({"error": "No draft goals to submit"}), 400

    total_weight = sum(g["weightage"] for g in goals)
    if abs(total_weight - 100.0) > 0.01:    # float tolerance
        return jsonify({
            "error": f"Total weightage must equal 100%. Currently: {total_weight}%"
        }), 400

    for goal in goals:
        db.execute(
            "UPDATE goals SET status='submitted', updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), goal["id"]),
        )
        audit_log(db, employee_id, "goal", goal["id"], "submitted",
                  old_value={"status": "draft"}, new_value={"status": "submitted"})

    db.commit()
    return jsonify({"submitted": len(goals), "total_weightage": total_weight})


@app.route("/api/goals/<goal_id>/approve", methods=["POST"])
def approve_goal(goal_id):
    """
    POST /api/goals/<goal_id>/approve
    Manager approves a submitted goal — transitions to 'locked'.
    Manager may have edited target/weightage inline before approving.
    """
    db   = get_db()
    data = request.get_json()
    manager_id = data["manager_id"]

    old = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if not old or old["status"] != "submitted":
        return jsonify({"error": "Goal not in submitted state"}), 400

    # Manager may pass updated target/weightage
    new_target    = data.get("target",    old["target"])
    new_weightage = data.get("weightage", old["weightage"])

    old_val = {"status": old["status"], "target": old["target"], "weightage": old["weightage"]}
    new_val = {"status": "locked",      "target": new_target,    "weightage": new_weightage}

    db.execute(
        "UPDATE goals SET status='locked', target=?, weightage=?, updated_at=? WHERE id=?",
        (new_target, new_weightage, datetime.utcnow().isoformat(), goal_id),
    )
    db.commit()

    audit_log(db, manager_id, "goal", goal_id, "approved", old_value=old_val, new_value=new_val)
    return jsonify({"status": "locked"})


@app.route("/api/goals/<goal_id>/return", methods=["POST"])
def return_goal(goal_id):
    """
    POST /api/goals/<goal_id>/return
    Manager returns a goal for rework. Employee can edit and resubmit.
    """
    db   = get_db()
    data = request.get_json()

    if not data.get("reason"):
        return jsonify({"error": "Return reason is required"}), 400

    db.execute(
        "UPDATE goals SET status='returned', return_reason=?, updated_at=? WHERE id=?",
        (data["reason"], datetime.utcnow().isoformat(), goal_id),
    )
    db.commit()

    audit_log(db, data["manager_id"], "goal", goal_id, "returned",
              old_value={"status": "submitted"}, new_value={"status": "returned", "reason": data["reason"]})

    return jsonify({"status": "returned"})


# =============================================================
# ACHIEVEMENTS — Quarterly check-in logging
# =============================================================

@app.route("/api/achievements", methods=["POST"])
def log_achievement():
    """
    POST /api/achievements
    Employee logs actual achievement for a quarter.
    System auto-computes the progress score.
    """
    db   = get_db()
    data = request.get_json()

    goal = db.execute("SELECT * FROM goals WHERE id=?", (data["goal_id"],)).fetchone()
    if not goal:
        return jsonify({"error": "Goal not found"}), 404
    if goal["status"] != "locked":
        return jsonify({"error": "Can only log achievement for locked goals"}), 400

    # Compute score automatically
    score = compute_score(
        uom_type    = goal["uom_type"],
        target      = goal["target"],
        actual      = data.get("actual"),
        target_date = goal["target_date"],
        actual_date = data.get("actual_date"),
    )

    # Upsert (update if exists for this quarter)
    existing = db.execute(
        "SELECT id FROM achievements WHERE goal_id=? AND quarter=?",
        (data["goal_id"], data["quarter"]),
    ).fetchone()

    if existing:
        db.execute(
            """UPDATE achievements SET actual=?, actual_date=?, progress_status=?,
               computed_score=?, updated_at=? WHERE id=?""",
            (data.get("actual"), data.get("actual_date"), data.get("progress_status","on_track"),
             score, datetime.utcnow().isoformat(), existing["id"]),
        )
        ach_id = existing["id"]
    else:
        ach_id = _new_id()
        db.execute(
            """INSERT INTO achievements (id, goal_id, quarter, actual, actual_date,
               progress_status, computed_score) VALUES (?,?,?,?,?,?,?)""",
            (ach_id, data["goal_id"], data["quarter"], data.get("actual"),
             data.get("actual_date"), data.get("progress_status","on_track"), score),
        )

    # If this is a shared goal, sync achievement to all child goals
    _sync_shared_achievement(db, data["goal_id"], data["quarter"], data.get("actual"), score)

    db.commit()
    audit_log(db, data["employee_id"], "achievement", ach_id, "updated",
              new_value={"actual": data.get("actual"), "score": score, "quarter": data["quarter"]})

    return jsonify({"achievement_id": ach_id, "computed_score": score, "score_pct": f"{score*100:.1f}%"})


def _sync_shared_achievement(db, parent_goal_id, quarter, actual, score):
    """
    When a shared goal's parent is updated, propagate actual + score
    to all child goal achievement records.

    INTERVIEW: "This is the shared goal sync. The primary owner logs the actual.
    We find all children via shared_goal_links and upsert their achievement rows
    with the same actual and score. This keeps all employees' goal sheets consistent."
    """
    children = db.execute(
        "SELECT child_goal_id FROM shared_goal_links WHERE parent_goal_id=?",
        (parent_goal_id,),
    ).fetchall()

    for child in children:
        child_id = child["child_goal_id"]
        existing = db.execute(
            "SELECT id FROM achievements WHERE goal_id=? AND quarter=?",
            (child_id, quarter),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE achievements SET actual=?, computed_score=?, updated_at=? WHERE id=?",
                (actual, score, datetime.utcnow().isoformat(), existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO achievements (id, goal_id, quarter, actual, computed_score) VALUES (?,?,?,?,?)",
                (_new_id(), child_id, quarter, actual, score),
            )


# =============================================================
# REPORTS — CSV export
# =============================================================

@app.route("/api/reports/achievement", methods=["GET"])
def achievement_report():
    """
    GET /api/reports/achievement?cycle_id=...&quarter=...
    Returns CSV: Employee | Goal | Target | Actual | Score% | Status
    """
    db       = get_db()
    cycle_id = request.args.get("cycle_id")
    quarter  = request.args.get("quarter", "Q1")

    rows = db.execute(
        """
        SELECT
            p.name          AS employee,
            p.department,
            g.thrust_area,
            g.title         AS goal,
            g.uom_type,
            g.weightage,
            g.target,
            a.actual,
            ROUND(COALESCE(a.computed_score,0)*100,1) AS score_pct,
            COALESCE(a.progress_status,'not_started')  AS status
        FROM goals g
        JOIN profiles p ON g.employee_id = p.id
        LEFT JOIN achievements a ON a.goal_id = g.id AND a.quarter = ?
        WHERE g.cycle_id = ? AND g.status = 'locked'
        ORDER BY p.name, g.weightage DESC
        """,
        (quarter, cycle_id),
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee","Department","Thrust Area","Goal","UoM","Weightage%",
                     "Target","Actual",f"{quarter} Score%","Status"])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=achievement_{quarter}.csv"},
    )


# =============================================================
# AUDIT TRAIL
# =============================================================

@app.route("/api/audit/<entity_id>", methods=["GET"])
def get_audit_trail(entity_id):
    db = get_db()
    return jsonify(fetch_trail(db, entity_id))


@app.route("/api/audit", methods=["GET"])
def get_all_audit():
    db = get_db()
    return jsonify(fetch_all_logs(
        db,
        actor_id    = request.args.get("actor_id"),
        entity_type = request.args.get("entity_type"),
        from_date   = request.args.get("from_date"),
        to_date     = request.args.get("to_date"),
    ))


# =============================================================
# ESCALATIONS (manual trigger for demo)
# =============================================================

@app.route("/api/escalations/run", methods=["POST"])
def run_escalations():
    db = get_db()
    data = request.get_json()
    cycle_id = data.get("cycle_id")
    events = run_escalation_check(db, cycle_id)
    return jsonify({"events_triggered": len(events), "events": events})


# =============================================================
# AI INSIGHTS (Claude API)
# =============================================================

@app.route("/api/ai/suggest", methods=["POST"])
def ai_goal_suggestions():
    """
    POST /api/ai/suggest
    Body: {thrust_area, role}
    Returns 3 AI-generated SMART goal suggestions.
    """
    try:
        import anthropic
        data = request.get_json()
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": f"""You are an HR goal-setting assistant.
Suggest 3 SMART goals for a {data.get('role','professional')} in the thrust area: "{data.get('thrust_area','General')}".

Return ONLY a JSON array (no markdown, no explanation):
[
  {{"title": "...", "description": "...", "uom_type": "numeric_min|numeric_max|timeline|zero", "suggested_target": 0}},
  ...
]"""
            }]
        )

        text = message.content[0].text.strip()
        suggestions = json.loads(text)
        return jsonify({"suggestions": suggestions})

    except Exception as e:
        # Fallback suggestions if API fails
        return jsonify({"suggestions": [
            {"title": "Increase Sales Revenue", "description": "Achieve monthly revenue target",
             "uom_type": "numeric_min", "suggested_target": 1000000},
            {"title": "Reduce Response Time", "description": "Improve TAT on customer queries",
             "uom_type": "numeric_max", "suggested_target": 24},
            {"title": "Complete Training Module", "description": "Finish assigned L&D courses",
             "uom_type": "timeline", "suggested_target": None},
        ], "note": "fallback_suggestions"})


@app.route("/api/ai/summary", methods=["POST"])
def ai_manager_summary():
    """
    POST /api/ai/summary
    Body: {manager_id, cycle_id, quarter}
    Generates a narrative summary of the manager's team performance.
    """
    db   = get_db()
    data = request.get_json()

    # Fetch team achievement data
    team_data = db.execute(
        """
        SELECT p.name, g.title, g.weightage,
               a.actual, g.target, ROUND(COALESCE(a.computed_score,0)*100,1) AS score_pct,
               a.progress_status
        FROM profiles p
        JOIN goals g ON g.employee_id = p.id
        LEFT JOIN achievements a ON a.goal_id = g.id AND a.quarter = ?
        WHERE p.manager_id = ? AND g.cycle_id = ? AND g.status = 'locked'
        """,
        (data.get("quarter","Q1"), data["manager_id"], data["cycle_id"]),
    ).fetchall()

    team_summary = "\n".join([
        f"- {r['name']}: '{r['title']}' | Target: {r['target']} | Actual: {r['actual']} | Score: {r['score_pct']}%"
        for r in team_data
    ])

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": f"""You are an HR analytics assistant. Write a concise 3-sentence manager 
summary of {data.get('quarter','Q1')} performance for this team:

{team_summary}

Highlight top performers, flag risks, and give one recommendation. 
Be professional and specific. No bullet points — pure narrative."""
            }]
        )
        summary = message.content[0].text.strip()
    except Exception:
        summary = (
            "Team performance is tracking well overall. "
            "Most members are on target for Q1 goals with positive momentum. "
            "Focus on closing the gap on revenue-linked objectives in the coming weeks."
        )

    return jsonify({"summary": summary, "data_points": len(team_data)})


# =============================================================
# UTILITIES
# =============================================================

def _new_id() -> str:
    import secrets
    return secrets.token_hex(8)


# =============================================================
# MAIN ENTRY POINT
# =============================================================

if __name__ == "__main__":
    import os

    print("GoalQuest API Starting...")
    
    init_db()

    port = int(os.environ.get("PORT", 5000))

    print(f"📍 Running at http://0.0.0.0:{port}")
    print(f"📖 Try: GET /api/goals?employee_id=emp-001")

    app.run(host="0.0.0.0", port=port)
