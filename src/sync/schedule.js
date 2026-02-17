const fs = require("fs");

function loadRoomDoorMapping(mappingFile) {
  const raw = fs.readFileSync(mappingFile, "utf8");
  return JSON.parse(raw);
}

function buildDesiredDoorSchedule({ events, mapping, nowIso }) {
  const items = [];

  const defaults = mapping.defaults || { unlockLeadMinutes: 15, unlockLagMinutes: 15 };

  for (const evt of events) {
    const roomName = evt.room;
    if (!roomName) continue;

    const doorKeys = mapping.rooms && mapping.rooms[roomName];
    if (!doorKeys || !Array.isArray(doorKeys) || doorKeys.length === 0) continue;

    for (const doorKey of doorKeys) {
      const door = mapping.doors && mapping.doors[doorKey];
      if (!door) continue;

      items.push({
        sourceEventId: evt.id,
        room: roomName,
        doorKey,
        doorLabel: door.label,
        unifiDoorIds: door.unifiDoorIds || [],
        startAt: evt.startAt,
        endAt: evt.endAt,
        unlockLeadMinutes: defaults.unlockLeadMinutes,
        unlockLagMinutes: defaults.unlockLagMinutes
      });
    }
  }

  return {
    generatedAt: nowIso,
    items
  };
}

module.exports = { loadRoomDoorMapping, buildDesiredDoorSchedule };
