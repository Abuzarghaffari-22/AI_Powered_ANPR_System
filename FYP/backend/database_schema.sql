-- =============================================================================
-- ANPR System — Database Schema
-- Database : anpr_db
-- Charset  : utf8mb4 / utf8mb4_unicode_ci
-- Engine   : InnoDB
-- =============================================================================

CREATE DATABASE IF NOT EXISTS anpr_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE anpr_db;

-- -----------------------------------------------------------------------------
-- Table: users
-- System users for login and role-based access.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INT           NOT NULL AUTO_INCREMENT,
    username      VARCHAR(100)  NOT NULL,
    password_hash VARCHAR(255)  NOT NULL,
    role          VARCHAR(50)   NOT NULL DEFAULT 'operator',
    created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP     NULL     DEFAULT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_username (username),
    KEY idx_username (username)
)
ENGINE = InnoDB
DEFAULT CHARSET = utf8mb4
COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- Table: vehicles
-- Registered vehicle registry with plate normalization and auth flag.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicles (
    id               INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    vehicle_id_code  VARCHAR(20)   NULL DEFAULT NULL,
    make             VARCHAR(60)   NULL DEFAULT NULL,
    model            VARCHAR(60)   NULL DEFAULT NULL,
    license_number   VARCHAR(40)   NOT NULL,
    license_normalized VARCHAR(40) NOT NULL,
    color            VARCHAR(40)   NULL DEFAULT NULL,
    engine_number    VARCHAR(60)   NULL DEFAULT NULL,
    chassis_number   VARCHAR(60)   NULL DEFAULT NULL,
    owner_name       VARCHAR(120)  NULL DEFAULT NULL,
    owner_cnic       VARCHAR(30)   NULL DEFAULT NULL,
    dues             VARCHAR(20)   NOT NULL DEFAULT 'Clear',
    image_filename   VARCHAR(120)  NULL DEFAULT NULL,
    status           VARCHAR(20)   NOT NULL DEFAULT 'Authorized',
    is_authorized    TINYINT(1)    NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Generated column: dash-stripped plate for fuzzy lookup
    license_stripped VARCHAR(50) GENERATED ALWAYS AS (
        REPLACE(REPLACE(license_normalized, '-', ''), ' ', '')
    ) STORED,

    PRIMARY KEY (id),
    UNIQUE KEY uq_plate_norm      (license_normalized),
    KEY idx_orig                  (license_number),
    KEY idx_auth                  (is_authorized),
    KEY idx_status                (status),
    KEY idx_img                   (image_filename),
    KEY idx_license_stripped      (license_stripped),
    KEY idx_auth_covering         (license_normalized, is_authorized, dues),
    FULLTEXT KEY ft_search        (owner_name, license_normalized, make, model)
)
ENGINE = InnoDB
DEFAULT CHARSET = utf8mb4
COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- Table: detection_logs
-- Every plate detection event recorded by the ANPR pipeline.
-- vehicle_id is nullable — unknown plates have no matching vehicle.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS detection_logs (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    detected_plate VARCHAR(40)   NOT NULL,
    matched_plate  VARCHAR(40)   NULL DEFAULT NULL,
    vehicle_id     INT UNSIGNED  NULL DEFAULT NULL,
    owner_name     VARCHAR(120)  NULL DEFAULT NULL,
    status         ENUM('authorized', 'unauthorized', 'unknown') NOT NULL DEFAULT 'unknown',
    confidence     FLOAT         NULL DEFAULT NULL,
    image_path     VARCHAR(255)  NULL DEFAULT NULL,
    detected_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_log_plate   (detected_plate),
    KEY idx_log_time    (detected_at),
    KEY idx_log_time_desc (detected_at DESC),
    KEY idx_log_status  (status),
    KEY idx_log_vid     (vehicle_id),
    KEY idx_cleanup     (detected_at, id),
    KEY idx_status_time (status, detected_at),

    CONSTRAINT fk_log_vehicle
        FOREIGN KEY (vehicle_id) REFERENCES vehicles (id) ON DELETE SET NULL,

    CONSTRAINT chk_confidence
        CHECK (confidence BETWEEN 0 AND 1)
)
ENGINE = InnoDB
DEFAULT CHARSET = utf8mb4
COLLATE = utf8mb4_unicode_ci;


-- =============================================================================
-- Views
-- =============================================================================

-- Recent detections joined with vehicle details (last 100 records)
CREATE OR REPLACE VIEW v_recent_detections AS
SELECT
    dl.id,
    dl.detected_plate,
    dl.matched_plate,
    dl.owner_name,
    dl.status,
    dl.confidence,
    dl.detected_at,
    v.make,
    v.model,
    v.color,
    v.is_authorized
FROM detection_logs dl
LEFT JOIN vehicles v ON dl.vehicle_id = v.id
ORDER BY dl.detected_at DESC
LIMIT 100;


-- Daily detection summary grouped by date
CREATE OR REPLACE VIEW v_daily_stats AS
SELECT
    CAST(detected_at AS DATE)                                       AS detection_date,
    COUNT(*)                                                        AS total_detections,
    SUM(CASE WHEN status = 'authorized'   THEN 1 ELSE 0 END)       AS authorized_count,
    SUM(CASE WHEN status = 'unauthorized' THEN 1 ELSE 0 END)       AS unauthorized_count,
    AVG(confidence)                                                 AS avg_confidence,
    MIN(detected_at)                                                AS first_detection,
    MAX(detected_at)                                                AS last_detection
FROM detection_logs
GROUP BY CAST(detected_at AS DATE)
ORDER BY detection_date DESC;


-- Vehicle registry totals
CREATE OR REPLACE VIEW v_vehicle_summary AS
SELECT
    COUNT(*)                                                        AS total_vehicles,
    SUM(CASE WHEN is_authorized = 1    THEN 1 ELSE 0 END)          AS authorized_vehicles,
    SUM(CASE WHEN is_authorized = 0    THEN 1 ELSE 0 END)          AS unauthorized_vehicles,
    SUM(CASE WHEN dues = 'Clear'       THEN 1 ELSE 0 END)          AS dues_clear,
    SUM(CASE WHEN dues != 'Clear'      THEN 1 ELSE 0 END)          AS dues_remaining
FROM vehicles;


-- =============================================================================
-- Stored Procedures
-- =============================================================================

DROP PROCEDURE IF EXISTS sp_cleanup_old_logs;

DELIMITER $$

CREATE PROCEDURE sp_cleanup_old_logs(IN days_to_keep INT)
BEGIN
    DECLARE deleted_count INT;

    DELETE FROM detection_logs
    WHERE detected_at < DATE_SUB(NOW(), INTERVAL days_to_keep DAY);

    SET deleted_count = ROW_COUNT();

    SELECT
        deleted_count                                               AS deleted_rows,
        CONCAT('Deleted logs older than ', days_to_keep, ' days')  AS message;
END$$

DELIMITER ;


DROP PROCEDURE IF EXISTS sp_get_detection_stats;

DELIMITER $$

CREATE PROCEDURE sp_get_detection_stats(
    IN start_date DATE,
    IN end_date   DATE
)
BEGIN
    SELECT
        DATE(detected_at)                                           AS date,
        COUNT(*)                                                    AS total_detections,
        SUM(CASE WHEN status = 'authorized'   THEN 1 ELSE 0 END)   AS authorized,
        SUM(CASE WHEN status = 'unauthorized' THEN 1 ELSE 0 END)   AS unauthorized,
        AVG(confidence)                                             AS avg_confidence,
        MIN(confidence)                                             AS min_confidence,
        MAX(confidence)                                             AS max_confidence
    FROM detection_logs
    WHERE DATE(detected_at) BETWEEN start_date AND end_date
    GROUP BY DATE(detected_at)
    ORDER BY date DESC;
END$$

DELIMITER ;


DROP PROCEDURE IF EXISTS sp_get_vehicle_stats;

DELIMITER $$

CREATE PROCEDURE sp_get_vehicle_stats()
BEGIN
    SELECT
        COUNT(*)                                                    AS total_vehicles,
        SUM(CASE WHEN is_authorized = 1  THEN 1 ELSE 0 END)        AS authorized,
        SUM(CASE WHEN is_authorized = 0  THEN 1 ELSE 0 END)        AS unauthorized,
        SUM(CASE WHEN dues = 'Clear'     THEN 1 ELSE 0 END)        AS dues_clear,
        SUM(CASE WHEN dues != 'Clear'    THEN 1 ELSE 0 END)        AS dues_pending,
        COUNT(DISTINCT make)                                        AS unique_makes,
        COUNT(DISTINCT color)                                       AS unique_colors
    FROM vehicles;
END$$

DELIMITER ;
