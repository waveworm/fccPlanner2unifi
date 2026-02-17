const fs = require("fs");
const path = require("path");
const dotenv = require("dotenv");
const Joi = require("joi");

function loadConfig() {
  dotenv.config();

  const schema = Joi.object({
    PORT: Joi.number().integer().min(1).max(65535).default(3000),
    BASE_URL: Joi.string().default("http://localhost:3000"),

    PCO_BASE_URL: Joi.string().uri().default("https://api.planningcenteronline.com"),
    PCO_AUTH_TYPE: Joi.string().valid("personal_access_token", "oauth").default("personal_access_token"),
    PCO_APP_ID: Joi.string().allow(""),
    PCO_SECRET: Joi.string().allow(""),
    PCO_ACCESS_TOKEN: Joi.string().allow(""),

    UNIFI_ACCESS_BASE_URL: Joi.string().uri().required(),
    UNIFI_ACCESS_VERIFY_TLS: Joi.string().valid("true", "false").default("false"),
    UNIFI_ACCESS_AUTH_TYPE: Joi.string().valid("none", "username_password", "api_token").default("none"),
    UNIFI_ACCESS_USERNAME: Joi.string().allow(""),
    UNIFI_ACCESS_PASSWORD: Joi.string().allow(""),
    UNIFI_ACCESS_API_TOKEN: Joi.string().allow(""),

    SYNC_CRON: Joi.string().default("*/5 * * * *"),
    SYNC_LOOKAHEAD_HOURS: Joi.number().integer().min(1).max(24 * 30).default(168),
    SYNC_LOOKBEHIND_HOURS: Joi.number().integer().min(0).max(24 * 7).default(2),
    ROOM_DOOR_MAPPING_FILE: Joi.string().default("./config/room-door-mapping.json")
  }).unknown();

  const { value, error } = schema.validate(process.env);
  if (error) {
    throw new Error(`Invalid environment configuration: ${error.message}`);
  }

  const mappingFile = path.resolve(process.cwd(), value.ROOM_DOOR_MAPPING_FILE);
  if (!fs.existsSync(mappingFile)) {
    throw new Error(`ROOM_DOOR_MAPPING_FILE does not exist: ${mappingFile}`);
  }

  return {
    port: value.PORT,
    baseUrl: value.BASE_URL,

    pco: {
      baseUrl: value.PCO_BASE_URL,
      authType: value.PCO_AUTH_TYPE,
      appId: value.PCO_APP_ID,
      secret: value.PCO_SECRET,
      accessToken: value.PCO_ACCESS_TOKEN
    },

    unifi: {
      baseUrl: value.UNIFI_ACCESS_BASE_URL,
      verifyTls: value.UNIFI_ACCESS_VERIFY_TLS === "true",
      authType: value.UNIFI_ACCESS_AUTH_TYPE,
      username: value.UNIFI_ACCESS_USERNAME,
      password: value.UNIFI_ACCESS_PASSWORD,
      apiToken: value.UNIFI_ACCESS_API_TOKEN
    },

    syncCron: value.SYNC_CRON,
    lookaheadHours: value.SYNC_LOOKAHEAD_HOURS,
    lookbehindHours: value.SYNC_LOOKBEHIND_HOURS,
    mappingFile
  };
}

module.exports = { loadConfig };
