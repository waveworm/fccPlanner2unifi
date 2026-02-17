const winston = require("winston");

function createLogger(_config) {
  return winston.createLogger({
    level: "info",
    format: winston.format.combine(
      winston.format.timestamp(),
      winston.format.errors({ stack: true }),
      winston.format.json()
    ),
    transports: [new winston.transports.Console()]
  });
}

module.exports = { createLogger };
