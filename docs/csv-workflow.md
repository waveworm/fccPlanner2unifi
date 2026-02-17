# Room/Door CSV + Google Sheets Workflow

This workflow lets you edit room-to-door assignments in CSV or Google Sheets, then generate JSON for the app.

## 1) Export current mapping to CSV

```bash
python3 tools/mapping_csv_tool.py export \
  --mapping config/room-door-mapping.json \
  --csv docs/room-door-mapping-template.csv
```

## 2) Edit in Google Sheets

1. Upload `docs/room-door-mapping-template.csv` to Google Drive.
2. Open with Google Sheets.
3. For columns `front_lobby`, `rear_lobby`, `gym_front`, add dropdown validation:
   - Select column (e.g. `front_lobby`)
   - Data -> Data validation -> Dropdown
   - Options: `yes`, *(blank)*
4. Mark `yes` for each door group a room should use.
5. Download as CSV.

## 3) Convert edited CSV back into JSON

```bash
python3 tools/mapping_csv_tool.py import \
  --mapping config/room-door-mapping.json \
  --csv /path/to/edited-room-door-mapping.csv \
  --out config/room-door-mapping.generated.json
```

Notes:
- `room` column is required.
- Door columns are detected from `doors` in the mapping JSON.
- Truthy values accepted: `yes`, `true`, `1`, `x`, `on`.

## 4) Apply generated JSON

After review, replace live mapping file:

```bash
cp config/room-door-mapping.generated.json config/room-door-mapping.json
./bin/service.sh restart
```
