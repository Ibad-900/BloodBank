-- ============================================================
--  Blood Donor Registry System — Peshawar
--  Milestone 5: DML Script
--  Covers: LOAD DATA INFILE · UPDATE · DELETE · Validation Queries
--  Student  : Ibad Ur Rahman | BS AI (B)
--  Course   : Database Systems Lab
-- ============================================================

USE blood_donor_registry_peshawar;

-- Allow local file loading (run once if not set globally)
SET GLOBAL local_infile = 1;

-- ──────────────────────────────────────────────────────────────
--  SECTION 1: LOAD DATA — populate all five tables from CSVs
--  Note: adjust file paths to match where CSVs are stored.
--  Insertion order matters: AREA and ADMIN_USER first (no FKs),
--  then DONOR (depends on AREA), then DONATION_RECORD and
--  BLOOD_REQUEST (depend on DONOR and ADMIN_USER).
-- ──────────────────────────────────────────────────────────────

-- 1a. AREA
LOAD DATA LOCAL INFILE 'csv/area.csv'
INTO TABLE AREA
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 ROWS
(area_id, area_name, city);

-- 1b. ADMIN_USER
LOAD DATA LOCAL INFILE 'csv/admin_user.csv'
INTO TABLE ADMIN_USER
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 ROWS
(admin_id, username, password_hash, full_name, created_on);

-- 1c. DONOR
LOAD DATA LOCAL INFILE 'csv/donor.csv'
INTO TABLE DONOR
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 ROWS
(donor_id, area_id, full_name, blood_group, age, gender,
 phone, is_available, registered_on);

-- 1d. DONATION_RECORD
-- Temporarily disable the 90-day trigger to allow bulk-loading
-- historical data that was already validated externally.
DROP TRIGGER IF EXISTS trg_check_donation_gap;

LOAD DATA LOCAL INFILE 'csv/donation_record.csv'
INTO TABLE DONATION_RECORD
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 ROWS
(donation_id, donor_id, donation_date, hospital_name,
 units_donated, notes);

-- Re-create the trigger after bulk load
DELIMITER $$
CREATE TRIGGER trg_check_donation_gap
BEFORE INSERT ON DONATION_RECORD
FOR EACH ROW
BEGIN
    DECLARE last_donation DATE;
    SELECT MAX(donation_date)
      INTO last_donation
      FROM DONATION_RECORD
     WHERE donor_id = NEW.donor_id;
    IF last_donation IS NOT NULL
       AND DATEDIFF(NEW.donation_date, last_donation) < 90
    THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT =
            'Donation rejected: donor must wait at least 90 days.';
    END IF;
END$$
DELIMITER ;

-- 1e. BLOOD_REQUEST
LOAD DATA LOCAL INFILE 'csv/blood_request.csv'
INTO TABLE BLOOD_REQUEST
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES  TERMINATED BY '\n'
IGNORE 1 ROWS
(request_id, admin_id, requester_name, blood_group_needed,
 urgency, contact_number, request_date, status);

-- ──────────────────────────────────────────────────────────────
--  SECTION 2: UPDATE OPERATIONS (with WHERE conditions)
-- ──────────────────────────────────────────────────────────────

-- UPDATE 1:
-- After 90 days pass from the last donation, restore donor availability.
-- This simulates the nightly job that re-enables eligible donors.
UPDATE DONOR d
SET    d.is_available = TRUE
WHERE  d.is_available = FALSE
  AND  90 <= (
       SELECT DATEDIFF(CURRENT_DATE, MAX(dr.donation_date))
       FROM   DONATION_RECORD dr
       WHERE  dr.donor_id = d.donor_id
  );

-- UPDATE 2:
-- Mark a specific blood request as Fulfilled once a matching
-- donor has been contacted and confirmed.
UPDATE BLOOD_REQUEST
SET    status = 'Fulfilled'
WHERE  request_id = 1
  AND  status = 'Pending';

-- UPDATE 3:
-- Correct a donor's phone number (data entry error fix).
UPDATE DONOR
SET    phone = '03001234567'
WHERE  donor_id = 5
  AND  phone != '03001234567';

-- ──────────────────────────────────────────────────────────────
--  SECTION 3: DELETE OPERATIONS (with WHERE conditions)
-- ──────────────────────────────────────────────────────────────

-- DELETE 1:
-- Remove cancelled blood requests older than 6 months to
-- keep the table clean (archiving policy).
DELETE FROM BLOOD_REQUEST
WHERE  status = 'Cancelled'
  AND  request_date < DATE_SUB(CURRENT_DATE, INTERVAL 6 MONTH);

-- DELETE 2:
-- Remove a duplicate/test donor record by phone number check.
-- Only deletes if the donor has no donation history (safe removal).
DELETE FROM DONOR
WHERE  phone = '03009999999'
  AND  donor_id NOT IN (
       SELECT DISTINCT donor_id FROM DONATION_RECORD
  );

-- ──────────────────────────────────────────────────────────────
--  SECTION 4: VALIDATION QUERIES
--  Run these after loading to confirm data integrity.
-- ──────────────────────────────────────────────────────────────

-- ── V1: Row counts for all tables ─────────────────────────────
SELECT 'AREA'            AS table_name, COUNT(*) AS row_count FROM AREA
UNION ALL
SELECT 'ADMIN_USER',                    COUNT(*)              FROM ADMIN_USER
UNION ALL
SELECT 'DONOR',                         COUNT(*)              FROM DONOR
UNION ALL
SELECT 'DONATION_RECORD',               COUNT(*)              FROM DONATION_RECORD
UNION ALL
SELECT 'BLOOD_REQUEST',                 COUNT(*)              FROM BLOOD_REQUEST;

-- ── V2: NULL check on critical columns ────────────────────────
SELECT 'DONOR — NULL blood_group'  AS check_name,
       COUNT(*) AS null_count
FROM   DONOR WHERE blood_group IS NULL
UNION ALL
SELECT 'DONOR — NULL phone',
       COUNT(*) FROM DONOR WHERE phone IS NULL
UNION ALL
SELECT 'DONOR — NULL area_id',
       COUNT(*) FROM DONOR WHERE area_id IS NULL
UNION ALL
SELECT 'DONATION_RECORD — NULL donor_id',
       COUNT(*) FROM DONATION_RECORD WHERE donor_id IS NULL
UNION ALL
SELECT 'DONATION_RECORD — NULL donation_date',
       COUNT(*) FROM DONATION_RECORD WHERE donation_date IS NULL
UNION ALL
SELECT 'BLOOD_REQUEST — NULL blood_group_needed',
       COUNT(*) FROM BLOOD_REQUEST WHERE blood_group_needed IS NULL
UNION ALL
SELECT 'BLOOD_REQUEST — NULL contact_number',
       COUNT(*) FROM BLOOD_REQUEST WHERE contact_number IS NULL;
-- Expected result: all null_count values should be 0.

-- ── V3: FK integrity check — DONOR → AREA ─────────────────────
SELECT d.donor_id, d.full_name, d.area_id
FROM   DONOR d
LEFT JOIN AREA a ON d.area_id = a.area_id
WHERE  a.area_id IS NULL;
-- Expected: 0 rows (every donor's area_id must exist in AREA).

-- ── V4: FK integrity check — DONATION_RECORD → DONOR ──────────
SELECT dr.donation_id, dr.donor_id
FROM   DONATION_RECORD dr
LEFT JOIN DONOR d ON dr.donor_id = d.donor_id
WHERE  d.donor_id IS NULL;
-- Expected: 0 rows.

-- ── V5: FK integrity check — BLOOD_REQUEST → ADMIN_USER ───────
SELECT br.request_id, br.admin_id
FROM   BLOOD_REQUEST br
LEFT JOIN ADMIN_USER a ON br.admin_id = a.admin_id
WHERE  br.admin_id IS NOT NULL
  AND  a.admin_id  IS NULL;
-- Expected: 0 rows.

-- ── V6: Business rule — no duplicate phone numbers in DONOR ───
SELECT phone, COUNT(*) AS cnt
FROM   DONOR
GROUP BY phone
HAVING cnt > 1;
-- Expected: 0 rows.

-- ── V7: Business rule — age in valid range (18–65) ────────────
SELECT donor_id, full_name, age
FROM   DONOR
WHERE  age NOT BETWEEN 18 AND 65;
-- Expected: 0 rows.

-- ── V8: Sample JOIN — available donors per blood group ─────────
SELECT   d.blood_group,
         a.area_name,
         COUNT(*) AS available_donors
FROM     DONOR d
JOIN     AREA  a ON d.area_id = a.area_id
WHERE    d.is_available = TRUE
GROUP BY d.blood_group, a.area_name
ORDER BY d.blood_group, available_donors DESC;

-- ── V9: Donors with more than one donation (active contributors)
SELECT   d.donor_id,
         d.full_name,
         d.blood_group,
         COUNT(dr.donation_id) AS total_donations,
         MAX(dr.donation_date) AS last_donated
FROM     DONOR d
JOIN     DONATION_RECORD dr ON d.donor_id = dr.donor_id
GROUP BY d.donor_id, d.full_name, d.blood_group
HAVING   total_donations > 1
ORDER BY total_donations DESC;

-- ── V10: Pending requests with no available matching donor ─────
SELECT br.request_id,
       br.requester_name,
       br.blood_group_needed,
       br.urgency,
       (SELECT COUNT(*)
        FROM   DONOR
        WHERE  blood_group    = br.blood_group_needed
          AND  is_available   = TRUE) AS matching_available_donors
FROM   BLOOD_REQUEST br
WHERE  br.status = 'Pending'
ORDER BY
  FIELD(br.urgency,'Critical','High','Normal'),
  br.request_date;
