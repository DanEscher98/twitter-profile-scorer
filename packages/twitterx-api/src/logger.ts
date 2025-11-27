import winston from "winston";

/**
 * Winston logger configured for AWS Lambda CloudWatch
 *
 * CloudWatch automatically captures stdout/stderr as log streams.
 * We use JSON format for structured logging which:
 * - Enables CloudWatch Insights queries
 * - Preserves log levels (INFO, WARN, ERROR)
 * - Includes timestamps and metadata
 */
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || "info",
  format: winston.format.combine(
    winston.format.timestamp({ format: "YYYY-MM-DD HH:mm:ss.SSS" }),
    winston.format.errors({ stack: true }),
    winston.format.json()
  ),
  defaultMeta: { service: "twitterx-api" },
  transports: [
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize({ all: process.env.NODE_ENV !== "production" }),
        process.env.NODE_ENV === "production"
          ? winston.format.json()
          : winston.format.printf(({ level, message, timestamp, service, ...meta }) => {
              const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : "";
              return `[${timestamp}] [${service}] ${level}: ${message}${metaStr}`;
            })
      ),
    }),
  ],
});

export default logger;
