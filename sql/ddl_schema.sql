-- ============================================================
--  Blood Donor Registry System — Peshawar
--  Milestone 4: DDL Script (CREATE TABLE + Constraints + Indexes)
--  Student  : Ibad Ur Rahman | BS AI (B)
--  Course   : Database Systems Lab
--  Date     : April 2026
-- ============================================================

CREATE DATABASE IF NOT EXISTS blood_donor_registry_peshawar
  CHARACTER SET utf8mb4
  COLLATE       utf8mb4_unicode_ci;

USE blood_donor_registry_peshawar;

-- ──────────────────────────────────────────────────────────────
--  TABLE 1: AREA
--  Stores Peshawar localities. Extracted from DONOR in 3NF to
--  eliminate the transitive dependency area_name → city.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS AREA (
    area_id   INT          NOT NULL AUTO_INCREMENT,
    area_name VARCHAR(100) NOT NULL,
    city      VARCHAR(80)  NOT NULL DEFAULT 'Peshawar',

    CONSTRAINT pk_area PRIMARY KEY (area_id),
    CONSTRAINT uq_area_name UNIQUE (area_name)
);

-- ──────────────────────────────────────────────────────────────
--  TABLE 2: ADMIN_USER
--  Blood bank staff accounts. Passwords stored as bcrypt hashes.
--  Must exist before BLOOD_REQUEST records are inserted.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ADMIN_USER (
    admin_id      INT          NOT NULL AUTO_INCREMENT,
    username      VARCHAR(50)  NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name     VARCHAR(100) NOT NULL,
    created_on    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_admin     PRIMARY KEY (admin_id),
    CONSTRAINT uq_username  UNIQUE      (username)
);

-- ──────────────────────────────────────────────────────────────
--  TABLE 3: DONOR
--  Core entity. Every non-key column depends directly on
--  donor_id only — satisfies 3NF. area_id is a FK so area
--  attributes live in AREA, not here.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DONOR (
    donor_id      INT         NOT NULL AUTO_INCREMENT,
    area_id       INT         NOT NULL,
    full_name     VARCHAR(100) NOT NULL,
    blood_group   ENUM('A+','A-','B+','B-','AB+','AB-','O+','O-') NOT NULL,
    age           TINYINT     NOT NULL,
    gender        ENUM('Male','Female','Other') NOT NULL,
    phone         VARCHAR(15) NOT NULL,
    is_available  BOOLEAN     NOT NULL DEFAULT TRUE,
    registered_on DATE        NOT NULL DEFAULT (CURRENT_DATE),

    CONSTRAINT pk_donor         PRIMARY KEY (donor_id),
    CONSTRAINT uq_donor_phone   UNIQUE      (phone),
    CONSTRAINT fk_donor_area    FOREIGN KEY (area_id)
        REFERENCES AREA(area_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,
    CONSTRAINT chk_donor_age    CHECK (age BETWEEN 18 AND 65)
);

-- Indexes for frequent search columns
CREATE INDEX idx_donor_blood_group   ON DONOR (blood_group);
CREATE INDEX idx_donor_area          ON DONOR (area_id);
CREATE INDEX idx_donor_is_available  ON DONOR (is_available);
-- Composite index: the most common query is "find available donors by blood group"
CREATE INDEX idx_donor_bg_avail      ON DONOR (blood_group, is_available);

-- ──────────────────────────────────────────────────────────────
--  TABLE 4: DONATION_RECORD
--  Each row records one blood donation event. References DONOR.
--  ON DELETE CASCADE: if a donor is removed, their history goes too.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS DONATION_RECORD (
    donation_id   INT          NOT NULL AUTO_INCREMENT,
    donor_id      INT          NOT NULL,
    donation_date DATE         NOT NULL,
    hospital_name VARCHAR(150) NOT NULL,
    units_donated TINYINT      NOT NULL DEFAULT 1,
    notes         TEXT,

    CONSTRAINT pk_donation        PRIMARY KEY (donation_id),
    CONSTRAINT fk_donation_donor  FOREIGN KEY (donor_id)
        REFERENCES DONOR(donor_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT chk_units          CHECK (units_donated > 0)
);

CREATE INDEX idx_donation_donor_id    ON DONATION_RECORD (donor_id);
CREATE INDEX idx_donation_date        ON DONATION_RECORD (donation_date);
-- Composite: used by the 90-day eligibility trigger
CREATE INDEX idx_donation_donor_date  ON DONATION_RECORD (donor_id, donation_date);

-- ──────────────────────────────────────────────────────────────
--  TABLE 5: BLOOD_REQUEST
--  Open requests from hospitals / patients. admin_id is SET NULL
--  on admin deletion so requests are not lost if an account is removed.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS BLOOD_REQUEST (
    request_id         INT         NOT NULL AUTO_INCREMENT,
    admin_id           INT,
    requester_name     VARCHAR(100) NOT NULL,
    blood_group_needed ENUM('A+','A-','B+','B-','AB+','AB-','O+','O-') NOT NULL,
    urgency            ENUM('Critical','High','Normal') NOT NULL DEFAULT 'Normal',
    contact_number     VARCHAR(15)  NOT NULL,
    request_date       DATE         NOT NULL DEFAULT (CURRENT_DATE),
    status             ENUM('Pending','Fulfilled','Cancelled') NOT NULL DEFAULT 'Pending',

    CONSTRAINT pk_request       PRIMARY KEY (request_id),
    CONSTRAINT fk_request_admin FOREIGN KEY (admin_id)
        REFERENCES ADMIN_USER(admin_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
);

CREATE INDEX idx_request_blood_group  ON BLOOD_REQUEST (blood_group_needed);
CREATE INDEX idx_request_status       ON BLOOD_REQUEST (status);
CREATE INDEX idx_request_admin        ON BLOOD_REQUEST (admin_id);

-- ──────────────────────────────────────────────────────────────
--  TRIGGER 1: Enforce 90-day gap between donations (same donor)
-- ──────────────────────────────────────────────────────────────
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
            'Donation rejected: donor must wait at least 90 days between donations.';
    END IF;
END$$

-- ──────────────────────────────────────────────────────────────
--  TRIGGER 2: Auto-mark donor unavailable after donation
-- ──────────────────────────────────────────────────────────────
CREATE TRIGGER trg_mark_unavailable
AFTER INSERT ON DONATION_RECORD
FOR EACH ROW
BEGIN
    UPDATE DONOR
       SET is_available = FALSE
     WHERE donor_id = NEW.donor_id;
END$$

DELIMITER ;

-- ──────────────────────────────────────────────────────────────
--  VERIFICATION QUERY — run after setup to confirm structure
-- ──────────────────────────────────────────────────────────────
-- SHOW TABLES;
-- SHOW CREATE TABLE DONOR;
-- SHOW CREATE TABLE DONATION_RECORD;
-- SHOW CREATE TABLE BLOOD_REQUEST;
