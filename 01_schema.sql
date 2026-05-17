-- =============================================================
-- GoalQuest: Database Schema
-- Run this file first: sqlite3 goalquest.db < 01_schema.sql
-- Or paste into any SQL editor / Supabase SQL editor
-- =============================================================

-- -------------------------------------------------------------
-- PROFILES: Extended user table
-- Who: every person using the system
-- Why: Supabase auth handles login, this table adds role + org structure
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profiles (
    id          TEXT PRIMARY KEY,           -- matches auth user ID (UUID)
    name        TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    role        TEXT NOT NULL               -- 'employee' | 'manager' | 'admin'
                CHECK (role IN ('employee','manager','admin')),
    manager_id  TEXT REFERENCES profiles(id), -- who is this person's L1 manager?
    department  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- CYCLES: Financial year / appraisal cycle configuration
-- Who: created by Admin
-- Why: Admin controls when each quarter's window opens
--      Without this, windows would be hardcoded — not flexible
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cycles (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)))),
    name                TEXT NOT NULL,      -- e.g. "FY2025-26"
    goal_setting_opens  TEXT NOT NULL,      -- ISO date: when employees can start creating goals
    q1_opens            TEXT NOT NULL,      -- when Q1 achievement entry opens
    q2_opens            TEXT NOT NULL,
    q3_opens            TEXT NOT NULL,
    q4_opens            TEXT NOT NULL,
    is_active           INTEGER DEFAULT 1   -- only 1 cycle active at a time
);

-- -------------------------------------------------------------
-- GOALS: The core entity — one row per goal per employee
-- Who: created by Employee, reviewed by Manager
-- Why: everything revolves around this — approval, tracking, scoring
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    employee_id     TEXT NOT NULL REFERENCES profiles(id),
    cycle_id        TEXT NOT NULL REFERENCES cycles(id),
    thrust_area     TEXT NOT NULL,          -- company strategic pillar, e.g. "Customer Satisfaction"
    title           TEXT NOT NULL,
    description     TEXT,
    
    -- UoM controls how progress score is computed
    uom_type        TEXT NOT NULL
                    CHECK (uom_type IN ('numeric_min','numeric_max','timeline','zero')),
                    -- numeric_min: higher actual = better (sales revenue)
                    -- numeric_max: lower actual = better (defect count, TAT)
                    -- timeline: date-based, was it delivered on time?
                    -- zero: success only if actual = 0 (safety incidents)
    
    target          REAL,                   -- numeric target (NULL for zero-based)
    target_date     TEXT,                   -- ISO date, used only for timeline UoM
    weightage       REAL NOT NULL           -- must be 10–100, sum across goals = 100
                    CHECK (weightage >= 10),
    
    -- State machine: draft → submitted → approved/returned → locked
    status          TEXT DEFAULT 'draft'
                    CHECK (status IN ('draft','submitted','approved','returned','locked')),
    
    -- Shared goal fields
    is_shared       INTEGER DEFAULT 0,      -- 1 if this was pushed by manager/admin
    shared_parent_id TEXT REFERENCES goals(id), -- points to parent if this is a child
    
    -- Manager return reason (when status = 'returned')
    return_reason   TEXT,
    
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- ACHIEVEMENTS: Quarterly actual values logged by employee
-- Who: Employee logs, Manager reviews and comments
-- Why: This is the "did you actually hit your goal?" data
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS achievements (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    goal_id         TEXT NOT NULL REFERENCES goals(id),
    quarter         TEXT NOT NULL
                    CHECK (quarter IN ('Q1','Q2','Q3','Q4')),
    
    actual          REAL,                   -- what the employee actually achieved
    actual_date     TEXT,                   -- for timeline UoM: when was it completed?
    
    progress_status TEXT DEFAULT 'not_started'
                    CHECK (progress_status IN ('not_started','on_track','completed')),
    
    -- Computed score (stored for performance, recomputed if formula changes)
    -- Range: 0.0 to 1.0 (display as %)
    computed_score  REAL,
    
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    
    UNIQUE(goal_id, quarter)                -- one achievement entry per goal per quarter
);

-- -------------------------------------------------------------
-- CHECKIN_COMMENTS: Manager's structured feedback per quarter
-- Who: Manager writes, Employee reads
-- Why: Documents the 1-on-1 discussion — audit-ready
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkin_comments (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    achievement_id  TEXT NOT NULL REFERENCES achievements(id),
    manager_id      TEXT NOT NULL REFERENCES profiles(id),
    comment         TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- SHARED_GOAL_LINKS: Maps parent goal → child goals
-- Why: When manager pushes a KPI to 10 employees, we store the links here
--      Achievement on parent syncs to all children via this table
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared_goal_links (
    parent_goal_id  TEXT NOT NULL REFERENCES goals(id),
    child_goal_id   TEXT NOT NULL REFERENCES goals(id),
    child_weightage REAL NOT NULL,          -- each recipient sets their own weightage
    PRIMARY KEY (parent_goal_id, child_goal_id)
);

-- -------------------------------------------------------------
-- AUDIT_LOGS: Immutable change log — append only, never delete
-- Who: System writes automatically on every mutation
-- Why: SOC-2 compliance, manager edits accountability, admin unlock tracking
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    actor_id    TEXT NOT NULL REFERENCES profiles(id),  -- who made the change
    entity_type TEXT NOT NULL,              -- 'goal' | 'achievement' | 'checkin'
    entity_id   TEXT NOT NULL,             -- which goal/achievement was changed
    action      TEXT NOT NULL,             -- 'created'|'submitted'|'approved'|'returned'|'edited'|'locked'|'unlocked'
    old_value   TEXT,                      -- JSON string of previous state
    new_value   TEXT,                      -- JSON string of new state
    timestamp   TEXT DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- ESCALATION_RULES: Configurable alert triggers (Admin sets these)
-- Why: Automates "chase" emails — reduces HR manual work
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS escalation_rules (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    trigger_event   TEXT NOT NULL,
                    -- 'goal_not_submitted': employee hasn't submitted N days after cycle opens
                    -- 'goal_not_approved': manager hasn't approved N days after submission
                    -- 'checkin_not_done': check-in not logged N days after quarter opens
    days_threshold  INTEGER NOT NULL,       -- how many days before escalating
    notify_level    INTEGER NOT NULL,       -- 1=notify employee, 2=notify manager, 3=notify HR
    is_active       INTEGER DEFAULT 1
);

-- =============================================================
-- INDEXES for query performance
-- =============================================================
CREATE INDEX IF NOT EXISTS idx_goals_employee ON goals(employee_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_achievements_goal ON achievements(goal_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id);
