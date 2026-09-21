# Layer 3 Geo Data Packs (PIN / Ward Polygon / SVAMITVA)

These files provide safe starter contracts for Layer 3 geo enrichment.

## Files
- `pin_geocode_index.schema.json` – JSON Schema for `PIN_GEOCODE_INDEX_JSON`
- `ward_polygons.schema.json` – JSON Schema for `WARD_POLYGONS_JSON`
- `svamitva_village_maps.schema.json` – JSON Schema for `SVAMITVA_VILLAGE_MAPS_JSON`
- `pin_geocode_index.sample.json` – sample data
- `ward_polygons.sample.json` – sample data
- `svamitva_village_maps.sample.json` – sample data

## Wiring to Env
Set these env vars as JSON strings (or inject through your config system):
- `PIN_GEOCODE_INDEX_JSON`
- `WARD_POLYGONS_JSON`
- `SVAMITVA_VILLAGE_MAPS_JSON`

## Notes for Ops
- Keep district names consistent with your runtime district master data.
- Ensure polygons are closed logically (first/last repeat is optional in this format).
- Keep coordinates in WGS84 decimal degrees.
- Validate against schema before rollout to production.
