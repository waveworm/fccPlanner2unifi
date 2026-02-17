#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

TRUTHY = {"1", "true", "yes", "y", "x", "on"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def export_csv(mapping_path: Path, csv_path: Path) -> None:
    mapping = load_json(mapping_path)
    doors = mapping.get("doors") or {}
    rooms = mapping.get("rooms") or {}

    door_keys = sorted(list(doors.keys()))

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["room"] + door_keys + ["notes"],
            extrasaction="ignore",
        )
        writer.writeheader()

        for room_name in sorted(rooms.keys()):
            assigned = set(rooms.get(room_name) or [])
            row = {"room": room_name, "notes": ""}
            for dk in door_keys:
                row[dk] = "yes" if dk in assigned else ""
            writer.writerow(row)


def parse_bool_cell(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in TRUTHY


def import_csv(mapping_path: Path, csv_path: Path, out_path: Path) -> None:
    mapping = load_json(mapping_path)
    doors = mapping.get("doors") or {}
    door_keys = sorted(list(doors.keys()))

    new_rooms: dict[str, list[str]] = {}

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "room" not in reader.fieldnames:
            raise ValueError("CSV must include a 'room' column")

        for row in reader:
            room_name = str(row.get("room", "")).strip()
            if not room_name:
                continue

            selected = [dk for dk in door_keys if parse_bool_cell(row.get(dk))]
            if selected:
                new_rooms[room_name] = selected

    out_mapping = {
        "doors": mapping.get("doors") or {},
        "rooms": dict(sorted(new_rooms.items(), key=lambda kv: kv[0].lower())),
        "defaults": mapping.get("defaults") or {},
    }
    dump_json(out_path, out_mapping)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export/import room-door mapping CSV.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export JSON mapping to CSV template")
    p_export.add_argument("--mapping", required=True, type=Path)
    p_export.add_argument("--csv", required=True, type=Path)

    p_import = sub.add_parser("import", help="Import CSV and produce JSON mapping")
    p_import.add_argument("--mapping", required=True, type=Path, help="Existing mapping file (for doors/defaults)")
    p_import.add_argument("--csv", required=True, type=Path)
    p_import.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()

    if args.command == "export":
        export_csv(args.mapping, args.csv)
        print(f"Wrote CSV template: {args.csv}")
    elif args.command == "import":
        import_csv(args.mapping, args.csv, args.out)
        print(f"Wrote JSON mapping: {args.out}")


if __name__ == "__main__":
    main()
