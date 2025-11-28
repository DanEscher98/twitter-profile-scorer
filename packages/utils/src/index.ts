/**
 * Shared logger using Winston
 *
 * Supports two modes controlled by APP_MODE environment variable:
 * - Local (default): Colorized, human-readable output
 * - Production (APP_MODE=production): JSON format optimized for CloudWatch
 *
 * Log level controlled by LOG_LEVEL environment variable:
 * - debug, info, warn, error, silent (default: info)
 * - "silent" disables all logging (checked at runtime, can be set after import)
 *
 * Usage:
 *   import { createLogger } from "@profile-scorer/utils";
 *   const log = createLogger("my-service");
 *   log.info("message", { key: "value" });
 *
 * Local output:
 *   [info] message { "key": "value" }
 *
 * Production output (CloudWatch):
 *   {"severity":"info","message":"message","service":"my-service","key":"value"}
 */

import winston from "winston";

/**
 * Check if logging is disabled (checked at runtime on each log call)
 */
function isSilent(): boolean {
  return process.env.LOG_LEVEL?.toLowerCase() === "silent";
}

/**
 * Check if running in production mode (AWS Lambda)
 */
function isProduction(): boolean {
  return process.env.APP_MODE === "production";
}

/**
 * Custom format that checks LOG_LEVEL=silent at runtime
 */
const silentFilter = winston.format((info) => {
  // Check silent mode on EVERY log call (runtime check)
  if (isSilent()) {
    return false; // Suppress the log
  }
  return info;
});

/**
 * Cache for logger instances by service name
 */
const loggerCache = new Map<string, winston.Logger>();

/**
 * Create a logger instance for a specific service
 *
 * @param service - Service name to include in all log entries
 * @returns Winston logger instance
 */
export function createLogger(service: string): winston.Logger {
  // Return cached logger if exists
  if (loggerCache.has(service)) {
    return loggerCache.get(service)!;
  }

  const consoleTransport = new winston.transports.Console({
    level: "debug", // Allow all levels, filtering done by silentFilter
    format: winston.format.combine(
      silentFilter(), // Runtime check for LOG_LEVEL=silent
      ...(isProduction()
        ? [
            // Production: no color, structured JSON logs for CloudWatch
            winston.format.printf(({ level, message, ...meta }) => {
              return JSON.stringify({
                severity: level,
                message,
                service,
                ...meta,
              });
            }),
          ]
        : [
            // Local: colorized, human-readable
            winston.format.colorize(),
            winston.format.timestamp({ format: "YYYY-MM-DD HH:mm:ss" }),
            winston.format.printf(({ level, message, ...meta }) => {
              delete meta.timestamp;
              return `[${level}] ${message} ${
                Object.keys(meta).length ? JSON.stringify(meta, null, 2) : ""
              }`;
            }),
          ])
    ),
  });

  const logger = winston.createLogger({
    level: "debug", // Allow all levels through, silentFilter handles suppression
    defaultMeta: { service },
    transports: [consoleTransport],
  });

  loggerCache.set(service, logger);
  return logger;
}

// Re-export winston types for convenience
export type { Logger } from "winston";
export default createLogger;
