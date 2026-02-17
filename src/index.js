const express = require("express");
const cron = require("node-cron");

const { loadConfig } = require("./lib/config");
const { createLogger } = require("./lib/logger");
const { SyncService } = require("./sync/sync-service");

async function main() {
  const config = loadConfig();
  const logger = createLogger(config);

  const app = express();
  app.use(express.json({ limit: "1mb" }));

  const syncService = new SyncService({ config, logger });

  app.get("/", async (_req, res) => {
    res.redirect("/dashboard");
  });

  app.get("/health", async (_req, res) => {
    res.json({ ok: true });
  });

  app.get("/dashboard", async (_req, res) => {
    const status = syncService.getStatusSnapshot();

    res
      .status(200)
      .type("html")
      .send(`<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PCO → UniFi Access Sync</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; }
    .k { color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .v { font-size: 14px; white-space: pre-wrap; word-break: break-word; }
    a { color: #2563eb; }
    button { border-radius: 10px; padding: 10px 12px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; }
  </style>
</head>
<body>
  <h1>PCO → UniFi Access Sync</h1>
  <p><a href="/api/status">JSON status</a></p>
  <div class="grid">
    <div class="card">
      <div class="k">Last Sync</div>
      <div class="v">${status.lastSyncAt ?? "(never)"}</div>
    </div>
    <div class="card">
      <div class="k">Last Result</div>
      <div class="v">${status.lastSyncResult ?? "(none)"}</div>
    </div>
    <div class="card">
      <div class="k">PCO</div>
      <div class="v">${status.pcoStatus}</div>
    </div>
    <div class="card">
      <div class="k">UniFi Access</div>
      <div class="v">${status.unifiStatus}</div>
    </div>
  </div>

  <div class="card" style="margin-top: 16px;">
    <div class="k">Recent Errors</div>
    <div class="v">${(status.recentErrors || []).join("\n") || "(none)"}</div>
  </div>

  <div style="margin-top: 16px; display: flex; gap: 12px;">
    <form method="post" action="/api/sync/run">
      <button type="submit">Run sync now</button>
    </form>
  </div>
</body>
</html>`);
  });

  app.get("/api/status", async (_req, res) => {
    res.json(syncService.getStatusSnapshot());
  });

  app.post("/api/sync/run", async (_req, res) => {
    try {
      await syncService.runOnce();
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ ok: false, error: String(err && err.message ? err.message : err) });
    }
  });

  cron.schedule(config.syncCron, async () => {
    try {
      await syncService.runOnce();
    } catch (err) {
      logger.error("Scheduled sync failed", { err: String(err && err.message ? err.message : err) });
    }
  });

  app.listen(config.port, () => {
    logger.info(`Server listening on http://localhost:${config.port}`);
    logger.info(`Sync schedule: ${config.syncCron}`);
  });

  // Kick once on startup.
  setTimeout(() => {
    syncService.runOnce().catch((err) => {
      logger.error("Startup sync failed", { err: String(err && err.message ? err.message : err) });
    });
  }, 500);
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
