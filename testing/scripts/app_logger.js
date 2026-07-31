/** Logger module for console.log migration workload. */

function createLogger(namespace) {
  return {
    info(msg) {
      console.log(`[${namespace}] INFO: ${msg}`);
    },
    warn(msg) {
      console.log(`[${namespace}] WARN: ${msg}`);
    },
    error(msg) {
      console.log(`[${namespace}] ERROR: ${msg}`);
    },
  };
}

function bootstrap() {
  console.log("Application starting...");
  const log = createLogger("app");
  log.info("Bootstrap complete");
}

module.exports = { createLogger, bootstrap };
