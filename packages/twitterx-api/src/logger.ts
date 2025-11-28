/**
 * Re-export shared logger configured for twitterx-api service
 */
import { createLogger } from "@profile-scorer/logger";

const logger = createLogger("twitterx-api");

export default logger;
