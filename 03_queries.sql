-- =============================================================
-- GoalQuest: Verification Queries
-- Run these after seeding to confirm everything is correct
-- =============================================================

-- 1. All users and their roles
SELECT name, email, role, department FROM profiles ORDER BY role;

-- 2. All goals with their employee and status
SELECT
    p.name        AS employee,
    g.thrust_area,
    g.title,
    g.uom_type,
    g.weightage   AS weight_pct,
    g.status,
    g.is_shared
FROM goals g
JOIN profiles p ON g.employee_id = p.id
ORDER BY p.name, g.weightage DESC;

-- 3. Weightage sum per employee (must equal 100 for submitted/approved goals)
SELECT
    p.name,
    SUM(g.weightage) AS total_weightage,
    COUNT(g.id)      AS goal_count,
    CASE WHEN SUM(g.weightage) = 100 THEN '✅ Valid' ELSE '❌ Invalid' END AS validation
FROM goals g
JOIN profiles p ON g.employee_id = p.id
WHERE g.status IN ('submitted','approved','locked')
GROUP BY p.id, p.name;

-- 4. Q1 achievements with computed scores (formatted as %)
SELECT
    p.name        AS employee,
    g.title,
    g.uom_type,
    g.target,
    a.actual,
    ROUND(a.computed_score * 100, 1) AS score_pct,
    a.progress_status
FROM achievements a
JOIN goals g ON a.goal_id = g.id
JOIN profiles p ON g.employee_id = p.id
WHERE a.quarter = 'Q1'
ORDER BY p.name;

-- 5. Audit trail for goal-a1 (shows manager edited it before approving)
SELECT
    p.name    AS actor,
    al.action,
    al.old_value,
    al.new_value,
    al.timestamp
FROM audit_logs al
JOIN profiles p ON al.actor_id = p.id
WHERE al.entity_id = 'goal-a1'
ORDER BY al.timestamp;

-- 6. Completion dashboard: who has done Q1 check-in?
SELECT
    p.name,
    COUNT(g.id)                                    AS total_goals,
    COUNT(a.id)                                    AS goals_with_q1_actual,
    CASE WHEN COUNT(g.id) = COUNT(a.id)
         THEN '✅ Done' ELSE '⏳ Pending' END      AS q1_checkin_status
FROM profiles p
JOIN goals g ON g.employee_id = p.id AND g.status = 'locked'
LEFT JOIN achievements a ON a.goal_id = g.id AND a.quarter = 'Q1'
WHERE p.role = 'employee'
GROUP BY p.id, p.name;

-- 7. Shared goal links
SELECT
    pg.title          AS parent_goal,
    p.name            AS child_employee,
    sgl.child_weightage
FROM shared_goal_links sgl
JOIN goals pg ON sgl.parent_goal_id = pg.id
JOIN goals cg ON sgl.child_goal_id = cg.id
JOIN profiles p ON cg.employee_id = p.id;

-- 8. Escalation rules configured by admin
SELECT trigger_event, days_threshold,
    CASE notify_level
        WHEN 1 THEN 'Notify Employee'
        WHEN 2 THEN 'Notify Manager'
        WHEN 3 THEN 'Notify HR'
    END AS notify_who,
    CASE is_active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS status
FROM escalation_rules
ORDER BY trigger_event, days_threshold;
