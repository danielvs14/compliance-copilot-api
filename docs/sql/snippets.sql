-- Compliance Copilot SQL scratchpad
-- Keep frequently-used investigative queries here for quick copy/paste.

-- 1. List organizations for a given user email
SELECT u.id       AS user_id,
       u.email,
       m.role,
       o.id       AS org_id,
       o.name     AS org_name,
       m.created_at
FROM users u
JOIN memberships m ON m.user_id = u.id
JOIN orgs o         ON o.id = m.org_id
WHERE LOWER(u.email) = LOWER('owner@example.com')
ORDER BY m.created_at DESC;

-- 2. Show documents uploaded by an org (including storage pointer)
SELECT d.id,
       d.title,
       d.storage_url,
       d.created_at,
       u.email AS uploaded_by
FROM documents d
JOIN users u ON u.id = d.uploaded_by
WHERE d.org_id = '96a8de36-1c43-4b54-9183-190078e8b78b'
ORDER BY d.created_at DESC;

-- 3. Requirements scoped to an org with status + confidence view
SELECT r.id,
       r.title_en,
       r.status,
       r.confidence,
       r.confidence_bucket,
       r.created_at
FROM requirements r
WHERE r.org_id = '96a8de36-1c43-4b54-9183-190078e8b78b'
ORDER BY r.created_at DESC
LIMIT 50;

-- 4. Recently uploaded permits and training certificates for an org
SELECT 'permit' AS record_type,
       p.id,
       p.title,
       p.issue_date,
       p.expiry_date,
       p.status
FROM permits p
WHERE p.org_id = '96a8de36-1c43-4b54-9183-190078e8b78b'

UNION ALL

SELECT 'training' AS record_type,
       t.id,
       t.title,
       t.issue_date,
       t.expiry_date,
       t.status
FROM training_certs t
WHERE t.org_id = '96a8de36-1c43-4b54-9183-190078e8b78b'
ORDER BY record_type, expiry_date;

-- 5. Lookup auth sessions for a user (useful when debugging login state)
SELECT s.id,
       s.user_id,
       s.org_id,
       s.created_at,
       s.expires_at,
       s.last_active_at
FROM user_sessions s
JOIN users u ON u.id = s.user_id
WHERE LOWER(u.email) = LOWER('owner@example.com')
ORDER BY s.created_at DESC;
