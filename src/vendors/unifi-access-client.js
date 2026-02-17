const axios = require("axios");

class UnifiAccessClient {
  constructor({ config, logger }) {
    this.config = config;
    this.logger = logger;

    this.http = axios.create({
      baseURL: config.unifi.baseUrl,
      timeout: 15000,
      // Many controllers use self-signed certs. Allow disabling verification via env.
      httpsAgent: config.unifi.verifyTls ? undefined : new (require("https").Agent)({ rejectUnauthorized: false })
    });
  }

  async checkConnectivity() {
    try {
      // We don’t assume a specific endpoint yet; just try to fetch the root.
      await this.http.get("/");
      return true;
    } catch (err) {
      this.logger.error("UniFi Access connectivity check failed", { err: String(err && err.message ? err.message : err) });
      return false;
    }
  }

  async applyDesiredSchedule(desired) {
    // Placeholder: next step is to map desired.items into UniFi Access schedules.
    this.logger.info("UniFi applyDesiredSchedule placeholder", {
      scheduleItems: desired.items.length
    });
  }
}

module.exports = { UnifiAccessClient };
