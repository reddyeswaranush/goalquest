-- =============================================================
-- GoalQuest: Demo Seed Data
-- Run after 01_schema.sql
-- Creates: 1 admin, 1 manager, 3 employees, 1 cycle, goals + achievements
-- =============================================================

-- USERS
INSERT OR IGNORE INTO profiles (id, name, email, role, manager_id, department) VALUES
    ('admin-001',    'Priya Sharma',   'priya@company.com',   'admin',    NULL,        'HR'),
    ('manager-001',  'Raj Mehta',      'raj@company.com',     'manager',  'admin-001', 'Sales'),
    ('emp-001',      'Anita Nair',     'anita@company.com',   'employee', 'manager-001','Sales'),
    ('emp-002',      'Dev Patel',      'dev@company.com',     'employee', 'manager-001','Sales'),
    ('emp-003',      'Sneha Rao',      'sneha@company.com',   'employee', 'manager-001','Sales');

-- ACTIVE CYCLE (FY 2025-26)
-- Dates set so Q1 window is currently open (for demo purposes)
INSERT OR IGNORE INTO cycles (id, name, goal_setting_opens, q1_opens, q2_opens, q3_opens, q4_opens, is_active) VALUES
    ('cycle-2526', 'FY2025-26', '2025-05-01', '2025-07-01', '2025-10-01', '2026-01-01', '2026-03-01', 1);

-- =============================================================
-- ANITA'S GOALS (3 goals, approved + locked)
-- =============================================================
INSERT OR IGNORE INTO goals (id, employee_id, cycle_id, thrust_area, title, description, uom_type, target, weightage, status) VALUES
    ('goal-a1', 'emp-001', 'cycle-2526',
     'Revenue Growth',
     'Achieve Q1 Sales Target',
     'Close deals totalling at least ₹50L in Q1',
     'numeric_min', 5000000, 40, 'locked'),

    ('goal-a2', 'emp-001', 'cycle-2526',
     'Customer Satisfaction',
     'Reduce Customer Complaint TAT',
     'Average resolution time must stay under 24 hours',
     'numeric_max', 24, 30, 'locked'),

    ('goal-a3', 'emp-001', 'cycle-2526',
     'Safety & Compliance',
     'Zero Safety Incidents',
     'Maintain zero workplace safety violations',
     'zero', NULL, 30, 'locked');

-- =============================================================
-- DEV'S GOALS (2 goals, still in draft — shows pending state)
-- =============================================================
INSERT OR IGNORE INTO goals (id, employee_id, cycle_id, thrust_area, title, description, uom_type, target, weightage, status) VALUES
    ('goal-d1', 'emp-002', 'cycle-2526',
     'Revenue Growth',
     'Upsell Existing Accounts',
     'Generate ₹20L from existing customer upsells',
     'numeric_min', 2000000, 60, 'draft'),

    ('goal-d2', 'emp-002', 'cycle-2526',
     'Product Delivery',
     'CRM Migration Project',
     'Complete migration of legacy CRM to Salesforce by deadline',
     'timeline', NULL, 40, 'draft');

-- Timeline goal: target_date updated separately
UPDATE goals SET target_date = '2025-09-30' WHERE id = 'goal-d2';

-- =============================================================
-- Q1 ACHIEVEMENTS for Anita's goals
-- =============================================================
INSERT OR IGNORE INTO achievements (id, goal_id, quarter, actual, progress_status, computed_score) VALUES
    -- Sales: achieved ₹42L of ₹50L target → 42/50 = 0.84
    ('ach-a1-q1', 'goal-a1', 'Q1', 4200000, 'on_track', 0.84),

    -- TAT: actual 18hrs vs target 24hrs → 24/18 = 1.33 (capped at 1.0)
    ('ach-a2-q1', 'goal-a2', 'Q1', 18,      'completed', 1.00),

    -- Zero-based: 0 incidents → 100%
    ('ach-a3-q1', 'goal-a3', 'Q1', 0,       'completed', 1.00);

-- =============================================================
-- MANAGER CHECK-IN COMMENT for Anita's Q1
-- =============================================================
INSERT OR IGNORE INTO checkin_comments (id, achievement_id, manager_id, comment) VALUES
    ('cc-001', 'ach-a1-q1', 'manager-001',
     'Anita is tracking well on sales. The TAT improvement is commendable. Focus on 2 large enterprise deals in Q2 to close the gap on revenue target.');

-- =============================================================
-- SHARED GOAL: Manager pushed a team-wide KPI
-- =============================================================
-- Parent goal (owned by manager)
INSERT OR IGNORE INTO goals (id, employee_id, cycle_id, thrust_area, title, description, uom_type, target, weightage, status, is_shared) VALUES
    ('goal-shared-parent', 'manager-001', 'cycle-2526',
     'Revenue Growth',
     'Team Q2 Revenue — ₹1.5Cr',
     'Entire team must collectively hit ₹1.5Cr in Q2',
     'numeric_min', 15000000, 20, 'locked', 1);

-- Child goals for each employee (title/target locked, weightage set by employee)
INSERT OR IGNORE INTO goals (id, employee_id, cycle_id, thrust_area, title, description, uom_type, target, weightage, status, is_shared, shared_parent_id) VALUES
    ('goal-shared-c1', 'emp-001', 'cycle-2526',
     'Revenue Growth', 'Team Q2 Revenue — ₹1.5Cr', 'Shared KPI from manager',
     'numeric_min', 15000000, 20, 'locked', 1, 'goal-shared-parent'),

    ('goal-shared-c2', 'emp-002', 'cycle-2526',
     'Revenue Growth', 'Team Q2 Revenue — ₹1.5Cr', 'Shared KPI from manager',
     'numeric_min', 15000000, 15, 'locked', 1, 'goal-shared-parent');

-- Link table
INSERT OR IGNORE INTO shared_goal_links VALUES ('goal-shared-parent','goal-shared-c1', 20);
INSERT OR IGNORE INTO shared_goal_links VALUES ('goal-shared-parent','goal-shared-c2', 15);

-- =============================================================
-- AUDIT LOG SAMPLES (what the system would auto-generate)
-- =============================================================
INSERT OR IGNORE INTO audit_logs (id, actor_id, entity_type, entity_id, action, old_value, new_value) VALUES
    ('log-001', 'emp-001',     'goal', 'goal-a1', 'created',
     NULL,
     '{"title":"Achieve Q1 Sales Target","weightage":40,"status":"draft"}'),

    ('log-002', 'emp-001',     'goal', 'goal-a1', 'submitted',
     '{"status":"draft"}',
     '{"status":"submitted"}'),

    ('log-003', 'manager-001', 'goal', 'goal-a1', 'edited',
     '{"target":4500000,"weightage":35}',
     '{"target":5000000,"weightage":40}'),   -- manager bumped target before approving

    ('log-004', 'manager-001', 'goal', 'goal-a1', 'approved',
     '{"status":"submitted"}',
     '{"status":"locked"}');

-- =============================================================
-- ESCALATION RULES (configured by Admin)
-- =============================================================
INSERT OR IGNORE INTO escalation_rules (id, trigger_event, days_threshold, notify_level) VALUES
    ('esc-001', 'goal_not_submitted', 7,  1),  -- Day 7: remind employee
    ('esc-002', 'goal_not_submitted', 14, 2),  -- Day 14: notify manager
    ('esc-003', 'goal_not_submitted', 21, 3),  -- Day 21: escalate to HR
    ('esc-004', 'goal_not_approved',  5,  2),  -- Day 5 after submission: nudge manager
    ('esc-005', 'checkin_not_done',   10, 1),  -- Day 10 into quarter: remind employee
    ('esc-006', 'checkin_not_done',   20, 2);  -- Day 20: notify manager
