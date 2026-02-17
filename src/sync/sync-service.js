const { DateTime } = require("./time");
const { loadRoomDoorMapping, buildDesiredDoorSchedule } = require("../sync/schedule");
const { PcoClient } = require("../vendors/pco-client");
const { UnifiAccessClient } = require("../vendors/unifi-access-client");

class SyncService {
  constructor({ config, logger }) {
    this.config = config;
    this.logger = logger;

    this.pco = new PcoClient({ config, logger });
    this.unifi = new UnifiAccessClient({ config, logger });

    this.status = {
      lastSyncAt: null,
      lastSyncResult: null,
      pcoStatus: "unknown",
      unifiStatus: "unknown",
      recentErrors: []
    };
  }

  getStatusSnapshot() {
    return { ...this.status };
  }

  _pushError(msg) {
    this.status.recentErrors = [msg, ...(this.status.recentErrors || [])].slice(0, 20);
  }

  async runOnce() {
    const startedAt = new Date().toISOString();
    this.status.lastSyncAt = startedAt;

    try {
      const mapping = loadRoomDoorMapping(this.config.mappingFile);

      const now = DateTime.nowUtc();
      const from = now.minusHours(this.config.lookbehindHours);
      const to = now.plusHours(this.config.lookaheadHours);

      const [pcoOk, unifiOk] = await Promise.all([
        this.pco.checkConnectivity(),
        this.unifi.checkConnectivity()
      ]);

      this.status.pcoStatus = pcoOk ? "ok" : "error";
      this.status.unifiStatus = unifiOk ? "ok" : "error";

      const events = await this.pco.getEvents({ fromIso: from.toIso(), toIso: to.toIso() });

      const desired = buildDesiredDoorSchedule({
        events,
        mapping,
        nowIso: now.toIso()
      });

      // For now we just compute and log. Next step is pushing schedules to UniFi.
      await this.unifi.applyDesiredSchedule(desired);

      this.status.lastSyncResult = `ok: events=${events.length} scheduleItems=${desired.items.length}`;

      this.logger.info("Sync complete", {
        events: events.length,
        scheduleItems: desired.items.length
      });
    } catch (err) {
      const msg = String(err && err.message ? err.message : err);
      this.status.lastSyncResult = `error: ${msg}`;
      this._pushError(`${new Date().toISOString()} ${msg}`);
      this.logger.error("Sync failed", { err: msg });
      throw err;
    }
  }
}

module.exports = { SyncService };
