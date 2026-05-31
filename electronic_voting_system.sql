-- Electronic Voting System — Pakistan
-- schema.sql
-- Run this file once to create the full database with seed data.

CREATE DATABASE IF NOT EXISTS electronic_voting_system;
USE electronic_voting_system;


-- ─────────────────────────────────────────────
-- ROLES & USERS
-- ─────────────────────────────────────────────

CREATE TABLE user_roles (
    role_id   INT AUTO_INCREMENT PRIMARY KEY,
    role_name VARCHAR(50)  NOT NULL UNIQUE,
    description VARCHAR(255)
);

CREATE TABLE ecp_admin (
    ecp_id        INT AUTO_INCREMENT PRIMARY KEY,
    full_name     VARCHAR(100) NOT NULL,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email         VARCHAR(100) NOT NULL UNIQUE,
    phone         VARCHAR(20),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP NULL,
    is_active     TINYINT(1) DEFAULT 1
);

CREATE TABLE provincial_officers (
    officer_id    INT AUTO_INCREMENT PRIMARY KEY,
    full_name     VARCHAR(100) NOT NULL,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email         VARCHAR(100) NOT NULL UNIQUE,
    phone         VARCHAR(20),
    province_id   INT NOT NULL,
    created_by_ecp INT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP NULL,
    is_active     TINYINT(1) DEFAULT 1
);

CREATE TABLE polling_officers (
    po_id             INT AUTO_INCREMENT PRIMARY KEY,
    full_name         VARCHAR(100) NOT NULL,
    username          VARCHAR(50)  NOT NULL UNIQUE,
    password_hash     VARCHAR(255) NOT NULL,
    phone             VARCHAR(20),
    station_id        INT NOT NULL,
    province_id       INT NOT NULL,
    created_by_officer INT NOT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login        TIMESTAMP NULL,
    is_active         TINYINT(1) DEFAULT 1
);

CREATE TABLE voters (
    voter_id           INT AUTO_INCREMENT PRIMARY KEY,
    cnic               VARCHAR(15) NOT NULL UNIQUE,
    full_name          VARCHAR(100) NOT NULL,
    date_of_birth      DATE NOT NULL,
    gender             ENUM('Male','Female','Other') NOT NULL,
    province_id        INT NOT NULL,
    city_id            INT NOT NULL,
    constituency_na_id INT NOT NULL,
    constituency_pa_id INT,
    has_voted          TINYINT(1) DEFAULT 0,
    registered_by      INT NOT NULL,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ─────────────────────────────────────────────
-- LOCATIONS
-- ─────────────────────────────────────────────

CREATE TABLE provinces (
    province_id          INT AUTO_INCREMENT PRIMARY KEY,
    province_name        VARCHAR(100) NOT NULL UNIQUE,
    province_code        VARCHAR(10)  NOT NULL UNIQUE,
    is_federal_territory TINYINT(1) DEFAULT 0
);

CREATE TABLE cities (
    city_id       INT AUTO_INCREMENT PRIMARY KEY,
    city_name     VARCHAR(100) NOT NULL,
    province_id   INT NOT NULL,
    UNIQUE KEY unique_city_province (city_name, province_id)
);

CREATE TABLE constituencies_na (
    na_id         INT AUTO_INCREMENT PRIMARY KEY,
    na_number     VARCHAR(20) NOT NULL UNIQUE,
    na_name       VARCHAR(100),
    city_id       INT NOT NULL,
    province_id   INT NOT NULL,
    total_voters  INT DEFAULT 0
);

CREATE TABLE constituencies_pa (
    pa_id         INT AUTO_INCREMENT PRIMARY KEY,
    pa_number     VARCHAR(20) NOT NULL UNIQUE,
    pa_name       VARCHAR(100),
    city_id       INT NOT NULL,
    province_id   INT NOT NULL,
    na_id         INT NOT NULL,
    total_voters  INT DEFAULT 0
);


-- ─────────────────────────────────────────────
-- ELECTION SETUP
-- ─────────────────────────────────────────────

CREATE TABLE elections (
    election_id    INT AUTO_INCREMENT PRIMARY KEY,
    election_name  VARCHAR(200) NOT NULL,
    election_date  DATE NOT NULL,
    status         ENUM('Upcoming','Active','Closed') DEFAULT 'Upcoming',
    created_by_ecp INT NOT NULL,
    opened_at      TIMESTAMP NULL,
    closed_at      TIMESTAMP NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE polling_stations (
    station_id        INT AUTO_INCREMENT PRIMARY KEY,
    station_name      VARCHAR(200) NOT NULL,
    address           TEXT NOT NULL,
    city_id           INT NOT NULL,
    province_id       INT NOT NULL,
    created_by_officer INT NOT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active         TINYINT(1) DEFAULT 1
);

CREATE TABLE station_assignments (
    assignment_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id    INT NOT NULL,
    po_id         INT NOT NULL,
    election_id   INT NOT NULL,
    assigned_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_station_election (station_id, election_id)
);

CREATE TABLE election_schedule (
    schedule_id        INT AUTO_INCREMENT PRIMARY KEY,
    election_id        INT NOT NULL,
    voting_start_time  TIME NOT NULL,
    voting_end_time    TIME NOT NULL,
    province_id        INT,
    notes              TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ─────────────────────────────────────────────
-- PARTIES & CANDIDATES
-- ─────────────────────────────────────────────

CREATE TABLE parties (
    party_id     INT AUTO_INCREMENT PRIMARY KEY,
    party_name   VARCHAR(200) NOT NULL UNIQUE,
    abbreviation VARCHAR(20)  NOT NULL UNIQUE,
    logo_path    VARCHAR(255),
    party_color  VARCHAR(10),
    founded_year YEAR,
    is_active    TINYINT(1) DEFAULT 1,
    created_by_ecp INT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE candidates (
    candidate_id    INT AUTO_INCREMENT PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    cnic            VARCHAR(15)  NOT NULL UNIQUE,
    photo_path      VARCHAR(255),
    party_id        INT NOT NULL,
    province_id     INT NOT NULL,
    added_by_officer INT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active       TINYINT(1) DEFAULT 1
);

-- Links a candidate to a National Assembly seat for a specific election
CREATE TABLE candidate_na (
    cna_id       INT AUTO_INCREMENT PRIMARY KEY,
    candidate_id INT NOT NULL,
    na_id        INT NOT NULL,
    election_id  INT NOT NULL,
    UNIQUE KEY unique_candidate_na (candidate_id, na_id, election_id)
);

-- Links a candidate to a Provincial Assembly seat for a specific election
CREATE TABLE candidate_pa (
    cpa_id       INT AUTO_INCREMENT PRIMARY KEY,
    candidate_id INT NOT NULL,
    pa_id        INT NOT NULL,
    election_id  INT NOT NULL,
    UNIQUE KEY unique_candidate_pa (candidate_id, pa_id, election_id)
);


-- ─────────────────────────────────────────────
-- VOTING & BALLOTS
-- ─────────────────────────────────────────────

-- ballot_token is a 64-char hex string (secrets.token_hex(32))
-- It links a vote to a ballot without revealing voter identity
CREATE TABLE ballots (
    ballot_id    INT AUTO_INCREMENT PRIMARY KEY,
    ballot_token VARCHAR(64) NOT NULL UNIQUE,
    election_id  INT NOT NULL,
    station_id   INT NOT NULL,
    issued_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE votes_na (
    vote_id      INT AUTO_INCREMENT PRIMARY KEY,
    ballot_id    INT NOT NULL UNIQUE,
    candidate_id INT NOT NULL,
    na_id        INT NOT NULL,
    election_id  INT NOT NULL,
    voted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE votes_pa (
    vote_id      INT AUTO_INCREMENT PRIMARY KEY,
    ballot_id    INT NOT NULL UNIQUE,
    candidate_id INT NOT NULL,
    pa_id        INT NOT NULL,
    election_id  INT NOT NULL,
    voted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tracks each voter's approval state at the polling station
-- status flow: pending → approved → voted  (or rejected)
CREATE TABLE voter_status (
    status_id    INT AUTO_INCREMENT PRIMARY KEY,
    cnic         VARCHAR(15) NOT NULL UNIQUE,
    election_id  INT NOT NULL,
    has_voted    TINYINT(1) DEFAULT 0,
    status       ENUM('pending','approved','rejected','voted') DEFAULT 'pending',
    voter_name   VARCHAR(100) NULL,
    voted_at     TIMESTAMP NULL,
    station_id   INT,
    po_approved_by INT
);


-- ─────────────────────────────────────────────
-- LOGS & SECURITY
-- ─────────────────────────────────────────────

CREATE TABLE login_logs (
    log_id     INT AUTO_INCREMENT PRIMARY KEY,
    username   VARCHAR(50) NOT NULL,
    role       VARCHAR(50) NOT NULL,
    ip_address VARCHAR(45),
    login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status     ENUM('Success','Failed') NOT NULL
);

-- Every admin action is recorded here (create election, add officer, import voters, etc.)
CREATE TABLE audit_trail (
    audit_id       INT AUTO_INCREMENT PRIMARY KEY,
    performed_by   VARCHAR(50) NOT NULL,
    role           VARCHAR(50) NOT NULL,
    action         VARCHAR(255) NOT NULL,
    table_affected VARCHAR(100),
    record_id      INT,
    details        TEXT,
    performed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE failed_attempts (
    attempt_id   INT AUTO_INCREMENT PRIMARY KEY,
    identifier   VARCHAR(100) NOT NULL,
    attempt_type ENUM('Login','CNIC') NOT NULL,
    ip_address   VARCHAR(45),
    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_blocked   TINYINT(1) DEFAULT 0
);


-- ─────────────────────────────────────────────
-- FOREIGN KEYS
-- ─────────────────────────────────────────────

ALTER TABLE provincial_officers
    ADD CONSTRAINT fk_po_province FOREIGN KEY (province_id)     REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_po_ecp      FOREIGN KEY (created_by_ecp)  REFERENCES ecp_admin(ecp_id);

ALTER TABLE polling_officers
    ADD CONSTRAINT fk_poff_station  FOREIGN KEY (station_id)          REFERENCES polling_stations(station_id),
    ADD CONSTRAINT fk_poff_province FOREIGN KEY (province_id)         REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_poff_created  FOREIGN KEY (created_by_officer)  REFERENCES provincial_officers(officer_id);

ALTER TABLE voters
    ADD CONSTRAINT fk_v_province FOREIGN KEY (province_id)        REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_v_city     FOREIGN KEY (city_id)            REFERENCES cities(city_id),
    ADD CONSTRAINT fk_v_na       FOREIGN KEY (constituency_na_id) REFERENCES constituencies_na(na_id),
    ADD CONSTRAINT fk_v_pa       FOREIGN KEY (constituency_pa_id) REFERENCES constituencies_pa(pa_id),
    ADD CONSTRAINT fk_v_registered FOREIGN KEY (registered_by)   REFERENCES provincial_officers(officer_id);

ALTER TABLE cities
    ADD CONSTRAINT fk_city_province FOREIGN KEY (province_id) REFERENCES provinces(province_id);

ALTER TABLE constituencies_na
    ADD CONSTRAINT fk_na_city     FOREIGN KEY (city_id)     REFERENCES cities(city_id),
    ADD CONSTRAINT fk_na_province FOREIGN KEY (province_id) REFERENCES provinces(province_id);

ALTER TABLE constituencies_pa
    ADD CONSTRAINT fk_pa_city     FOREIGN KEY (city_id)     REFERENCES cities(city_id),
    ADD CONSTRAINT fk_pa_province FOREIGN KEY (province_id) REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_pa_na       FOREIGN KEY (na_id)       REFERENCES constituencies_na(na_id);

ALTER TABLE elections
    ADD CONSTRAINT fk_elec_ecp FOREIGN KEY (created_by_ecp) REFERENCES ecp_admin(ecp_id);

ALTER TABLE polling_stations
    ADD CONSTRAINT fk_st_city     FOREIGN KEY (city_id)            REFERENCES cities(city_id),
    ADD CONSTRAINT fk_st_province FOREIGN KEY (province_id)        REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_st_created  FOREIGN KEY (created_by_officer) REFERENCES provincial_officers(officer_id);

ALTER TABLE station_assignments
    ADD CONSTRAINT fk_sa_station  FOREIGN KEY (station_id)  REFERENCES polling_stations(station_id),
    ADD CONSTRAINT fk_sa_po       FOREIGN KEY (po_id)       REFERENCES polling_officers(po_id),
    ADD CONSTRAINT fk_sa_election FOREIGN KEY (election_id) REFERENCES elections(election_id);

ALTER TABLE election_schedule
    ADD CONSTRAINT fk_es_election FOREIGN KEY (election_id) REFERENCES elections(election_id),
    ADD CONSTRAINT fk_es_province FOREIGN KEY (province_id) REFERENCES provinces(province_id);

ALTER TABLE parties
    ADD CONSTRAINT fk_party_ecp FOREIGN KEY (created_by_ecp) REFERENCES ecp_admin(ecp_id);

ALTER TABLE candidates
    ADD CONSTRAINT fk_cand_party   FOREIGN KEY (party_id)         REFERENCES parties(party_id),
    ADD CONSTRAINT fk_cand_province FOREIGN KEY (province_id)     REFERENCES provinces(province_id),
    ADD CONSTRAINT fk_cand_officer FOREIGN KEY (added_by_officer) REFERENCES provincial_officers(officer_id);

ALTER TABLE candidate_na
    ADD CONSTRAINT fk_cna_candidate FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    ADD CONSTRAINT fk_cna_na        FOREIGN KEY (na_id)        REFERENCES constituencies_na(na_id),
    ADD CONSTRAINT fk_cna_election  FOREIGN KEY (election_id)  REFERENCES elections(election_id);

ALTER TABLE candidate_pa
    ADD CONSTRAINT fk_cpa_candidate FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    ADD CONSTRAINT fk_cpa_pa        FOREIGN KEY (pa_id)        REFERENCES constituencies_pa(pa_id),
    ADD CONSTRAINT fk_cpa_election  FOREIGN KEY (election_id)  REFERENCES elections(election_id);

ALTER TABLE ballots
    ADD CONSTRAINT fk_bal_election FOREIGN KEY (election_id) REFERENCES elections(election_id),
    ADD CONSTRAINT fk_bal_station  FOREIGN KEY (station_id)  REFERENCES polling_stations(station_id);

ALTER TABLE votes_na
    ADD CONSTRAINT fk_vna_ballot    FOREIGN KEY (ballot_id)    REFERENCES ballots(ballot_id),
    ADD CONSTRAINT fk_vna_candidate FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    ADD CONSTRAINT fk_vna_na        FOREIGN KEY (na_id)        REFERENCES constituencies_na(na_id),
    ADD CONSTRAINT fk_vna_election  FOREIGN KEY (election_id)  REFERENCES elections(election_id);

ALTER TABLE votes_pa
    ADD CONSTRAINT fk_vpa_ballot    FOREIGN KEY (ballot_id)    REFERENCES ballots(ballot_id),
    ADD CONSTRAINT fk_vpa_candidate FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    ADD CONSTRAINT fk_vpa_pa        FOREIGN KEY (pa_id)        REFERENCES constituencies_pa(pa_id),
    ADD CONSTRAINT fk_vpa_election  FOREIGN KEY (election_id)  REFERENCES elections(election_id);

ALTER TABLE voter_status
    ADD CONSTRAINT fk_vs_election FOREIGN KEY (election_id)  REFERENCES elections(election_id),
    ADD CONSTRAINT fk_vs_station  FOREIGN KEY (station_id)   REFERENCES polling_stations(station_id),
    ADD CONSTRAINT fk_vs_po       FOREIGN KEY (po_approved_by) REFERENCES polling_officers(po_id);


-- ─────────────────────────────────────────────
-- SEED DATA
-- ─────────────────────────────────────────────

INSERT INTO user_roles (role_name, description) VALUES
    ('ECP',               'Election Commission of Pakistan — full system control'),
    ('Provincial Officer','Manages one province only'),
    ('Polling Officer',   'Verifies voters at polling station'),
    ('Voter',             'Casts vote via CNIC');

INSERT INTO provinces (province_name, province_code, is_federal_territory) VALUES
    ('Punjab',              'PB',  0),
    ('Sindh',               'SD',  0),
    ('Balochistan',         'BL',  0),
    ('Khyber Pakhtunkhwa',  'KPK', 0),
    ('Islamabad',           'ICT', 1);

INSERT INTO cities (city_name, province_id) VALUES
    ('Lahore',     1), ('Rawalpindi', 1),
    ('Karachi',    2), ('Hyderabad',  2),
    ('Quetta',     3), ('Gwadar',     3),
    ('Peshawar',   4), ('Haripur',    4),
    ('Islamabad',  5);

INSERT INTO constituencies_na (na_number, na_name, city_id, province_id) VALUES
    ('NA-118', 'Lahore-I',       1, 1), ('NA-119', 'Lahore-II',      1, 1), ('NA-120', 'Lahore-III',     1, 1),
    ('NA-50',  'Rawalpindi-I',   2, 1), ('NA-51',  'Rawalpindi-II',  2, 1), ('NA-52',  'Rawalpindi-III', 2, 1),
    ('NA-241', 'Karachi-I',      3, 2), ('NA-242', 'Karachi-II',     3, 2), ('NA-243', 'Karachi-III',    3, 2),
    ('NA-219', 'Hyderabad-I',    4, 2), ('NA-220', 'Hyderabad-II',   4, 2), ('NA-221', 'Hyderabad-III',  4, 2),
    ('NA-259', 'Quetta-I',       5, 3), ('NA-260', 'Quetta-II',      5, 3),
    ('NA-272', 'Gwadar',         6, 3),
    ('NA-1',   'Peshawar-I',     7, 4), ('NA-2',   'Peshawar-II',    7, 4), ('NA-3',   'Peshawar-III',   7, 4),
    ('NA-18',  'Haripur',        8, 4),
    ('NA-48',  'Islamabad-I',    9, 5), ('NA-49',  'Islamabad-II',   9, 5);

INSERT INTO constituencies_pa (pa_number, pa_name, city_id, province_id, na_id) VALUES
    ('PP-140', 'Lahore PA-I',       1, 1,  1), ('PP-141', 'Lahore PA-II',      1, 1,  1), ('PP-142', 'Lahore PA-III',     1, 1,  2),
    ('PP-8',   'Rawalpindi PA-I',   2, 1,  4), ('PP-9',   'Rawalpindi PA-II',  2, 1,  5), ('PP-10',  'Rawalpindi PA-III', 2, 1,  6),
    ('PS-100', 'Karachi PA-I',      3, 2,  7), ('PS-101', 'Karachi PA-II',     3, 2,  8), ('PS-102', 'Karachi PA-III',    3, 2,  9),
    ('PS-60',  'Hyderabad PA-I',    4, 2, 10), ('PS-61',  'Hyderabad PA-II',   4, 2, 11),
    ('PB-38',  'Quetta PA-I',       5, 3, 13), ('PB-39',  'Quetta PA-II',      5, 3, 14), ('PB-40',  'Quetta PA-III',     5, 3, 14),
    ('PB-51',  'Gwadar PA-I',       6, 3, 15), ('PB-52',  'Gwadar PA-II',      6, 3, 15),
    ('PK-1',   'Peshawar PA-I',     7, 4, 16), ('PK-2',   'Peshawar PA-II',    7, 4, 17), ('PK-3',   'Peshawar PA-III',   7, 4, 18),
    ('PK-40',  'Haripur PA-I',      8, 4, 19), ('PK-41',  'Haripur PA-II',     8, 4, 19), ('PK-42',  'Haripur PA-III',    8, 4, 19);

-- Default ECP admin account
-- Password: admin123  (sha256 hash)
-- Change this password immediately after first login
INSERT INTO ecp_admin (full_name, username, password_hash, email, phone) VALUES (
    'ECP Administrator',
    'ecp_admin',
    '44b13bbe247c9ad01afc7be7642b472dffd44ad3084d977352ae4112364b90da',
    'ecp@pakistan.gov.pk',
    '051-1234567'
);

-- Parties are seeded here as reference data.
-- ECP can manage them further through the dashboard after login.
-- Note: created_by_ecp=1 assumes the default ecp_admin above has ecp_id=1
INSERT INTO parties (party_name, abbreviation, party_color, created_by_ecp) VALUES
    ('Pakistan Tehreek-e-Insaf',    'PTI',   '#c94040', 1),
    ('Pakistan Muslim League (N)',  'PMLN',  '#1a5c2e', 1),
    ('Pakistan Peoples Party',      'PPP',   '#3a3a8a', 1),
    ('Muttahida Qaumi Movement',    'MQM',   '#b8963e', 1),
    ('Jamiat Ulema-e-Islam',        'JUI-F', '#1e6b8a', 1);
