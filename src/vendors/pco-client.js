const axios = require("axios");

class PcoClient {
  constructor({ config, logger }) {
    this.config = config;
    this.logger = logger;

    this.http = axios.create({
      baseURL: config.pco.baseUrl,
      timeout: 15000
    });
  }

  _applyAuth(headers) {
    const { authType, appId, secret, accessToken } = this.config.pco;

    if (authType === "personal_access_token") {
      if (!appId || !secret) {
        throw new Error("PCO_APP_ID and PCO_SECRET are required for personal_access_token auth");
      }
      const token = Buffer.from(`${appId}:${secret}`).toString("base64");
      headers.Authorization = `Basic ${token}`;
      return;
    }

    if (authType === "oauth") {
      if (!accessToken) {
        throw new Error("PCO_ACCESS_TOKEN is required for oauth auth");
      }
      headers.Authorization = `Bearer ${accessToken}`;
      return;
    }

    throw new Error(`Unsupported PCO auth type: ${authType}`);
  }

  async checkConnectivity() {
    try {
      const headers = {};
      this._applyAuth(headers);
      await this.http.get("/people/v2/people", { headers, params: { per_page: 1 } });
      return true;
    } catch (err) {
      this.logger.error("PCO connectivity check failed", { err: String(err && err.message ? err.message : err) });
      return false;
    }
  }

  async getEvents({ fromIso, toIso }) {
    // NOTE: This is a placeholder. We’ll adapt once you confirm whether you’re using Calendar or Services.
    // For now we return an empty list so the service can run end-to-end.
    this.logger.info("PCO getEvents placeholder", { fromIso, toIso });
    return [];
  }
}

module.exports = { PcoClient };
