CREATE DATABASE IF NOT EXISTS `locum_booking`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `locum_booking`;

CREATE TABLE IF NOT EXISTS `accounts` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `ic_number` VARCHAR(12) NOT NULL UNIQUE,
  `last4` VARCHAR(4) NOT NULL,
  `name` VARCHAR(150) DEFAULT NULL,
  `role` VARCHAR(20) DEFAULT 'MO',
  `reg_no` VARCHAR(50) DEFAULT NULL,
  `workplace` VARCHAR(150) DEFAULT NULL,
  `phone` VARCHAR(30) DEFAULT NULL,
  `email` VARCHAR(150) DEFAULT NULL,
  `last_login_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `telegram_settings` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `account_ic` VARCHAR(12) NOT NULL UNIQUE,
  `bot_token` VARCHAR(200) NOT NULL DEFAULT '',
  `chat_id` VARCHAR(80) NOT NULL DEFAULT '',
  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
  `notify_on_success` TINYINT(1) NOT NULL DEFAULT 1,
  `notify_on_fail` TINYINT(1) NOT NULL DEFAULT 1,
  `notify_on_dry_run` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `booking_jobs` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `account_ic` VARCHAR(12) NOT NULL,
  `title` VARCHAR(150) NOT NULL,
  `mode` ENUM('scheduled', 'monitor') NOT NULL DEFAULT 'scheduled',
  `target_dates` TEXT NULL,
  `target_slots` TEXT NULL,
  `schedule_time` VARCHAR(8) NULL,
  `schedule_tz` VARCHAR(10) NOT NULL DEFAULT 'MYT',
  `schedule_date` DATE NULL,
  `next_run_at` DATETIME NULL,
  `poll_interval` DECIMAL(5,2) NOT NULL DEFAULT 2.00,
  `max_bookings` INT NOT NULL DEFAULT 1,
  `one_per_day` TINYINT(1) NOT NULL DEFAULT 0,
  `dry_run` TINYINT(1) NOT NULL DEFAULT 0,
  `status` ENUM('pending', 'waiting', 'running', 'completed', 'stopped', 'error') NOT NULL DEFAULT 'pending',
  `success_count` INT NOT NULL DEFAULT 0,
  `last_message` VARCHAR(255) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `idx_jobs_ic_status` (`account_ic`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `booking_results` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `job_id` INT NULL,
  `account_ic` VARCHAR(12) NOT NULL,
  `account_name` VARCHAR(150) NULL,
  `account_role` VARCHAR(20) NULL,
  `workplace` VARCHAR(150) NULL,
  `job_title` VARCHAR(150) NULL,
  `mode` VARCHAR(30) NULL,
  `outcome` ENUM('SUCCESS', 'FAILED', 'NO_SLOT', 'DRY_RUN') NOT NULL DEFAULT 'SUCCESS',
  `booking_ref` VARCHAR(50) NULL,
  `slot_date` VARCHAR(120) NOT NULL,
  `slot_key` VARCHAR(120) NOT NULL,
  `slot_label` VARCHAR(120) NULL,
  `seat` VARCHAR(20) NULL,
  `reason` TEXT NULL,
  `telegram_sent` TINYINT(1) NOT NULL DEFAULT 0,
  `telegram_info` VARCHAR(255) NULL,
  `is_dry_run` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_results_ic` (`account_ic`, `created_at`),
  INDEX `idx_results_outcome` (`account_ic`, `outcome`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `bot_logs` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `job_id` INT NULL,
  `account_ic` VARCHAR(12) NULL,
  `level` ENUM('INFO', 'SUCCESS', 'WARN', 'ERROR') NOT NULL DEFAULT 'INFO',
  `message` TEXT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_logs_job_id` (`job_id`, `id`),
  INDEX `idx_logs_ic_id` (`account_ic`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
