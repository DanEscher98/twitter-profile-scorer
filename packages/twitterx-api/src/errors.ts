/**
 * Standardized Error Codes for TwitterX API
 *
 * Maps descriptive error names to HTTP status codes.
 * Same HTTP code can mean different things by context.
 */

export const ErrorCodes = {
  // API Authentication & Quota
  API_KEY_MISSING: 401,
  QUOTA_EXCEEDED: 429,
  RATE_LIMITED: 429,

  // User-related errors
  USER_NOT_FOUND: 404,
  USER_SUSPENDED: 403,
  USER_PROTECTED: 403,

  // Network & Retry errors
  MAX_RETRIES_EXCEEDED: 503,
  NETWORK_ERROR: 502,
  API_BOTTLENECK: 503,
  TIMEOUT: 504,

  // Response parsing errors
  INVALID_RESPONSE: 500,
  EMPTY_RESPONSE: 204,

  // Database errors
  DB_CONNECTION_ERROR: 500,
  DB_QUERY_ERROR: 500,
} as const;

export type ErrorCode = keyof typeof ErrorCodes;

/**
 * Custom error class for TwitterX API errors.
 */
export class TwitterXApiError extends Error {
  public readonly errorCode: ErrorCode;
  public readonly httpStatus: number;
  public readonly context?: Record<string, unknown>;

  constructor(errorCode: ErrorCode, message: string, context?: Record<string, unknown>) {
    super(`${errorCode}: ${message}`);
    this.name = "TwitterXApiError";
    this.errorCode = errorCode;
    this.httpStatus = ErrorCodes[errorCode];
    this.context = context;

    Error.captureStackTrace?.(this, TwitterXApiError);
  }

  /**
   * Check if error matches a specific error code
   */
  is(code: ErrorCode): boolean {
    return this.errorCode === code;
  }

  /**
   * Convert to JSON for logging
   */
  toJSON() {
    return {
      name: this.name,
      errorCode: this.errorCode,
      httpStatus: this.httpStatus,
      message: this.message,
      context: this.context,
    };
  }
}
