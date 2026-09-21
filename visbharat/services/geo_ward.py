from __future__ import annotations
import math
import re
import time


def _to_float(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _extract_ward_from_text(text='', address=''):
    match = re.search(r'\bward\s*(?:no\.?\s*)?[#:\-]?\s*([0-9]+[A-Za-z]?)\b',
                      f'{text or ""} {address or ""}', re.IGNORECASE)
    return match.group(1) if match else ''


def _resolve_pin_lat_lng(pin='', pin_index=None):
    row = (pin_index or {}).get(str(pin or '').strip(), {}) if isinstance(pin_index, dict) else {}
    if not isinstance(row, dict):
        row = {}
    return (_to_float(row.get('lat')), _to_float(row.get('lng')),
            str(row.get('district') or '').strip(), str(row.get('village') or '').strip())


def _point_in_polygon(lat, lng, points):
    """Ray casting for configured lat/lng vertices; includes boundary points."""
    vertices = []
    for point in points:
        if not isinstance(point, dict):
            return False
        y, x = _to_float(point.get('lat')), _to_float(point.get('lng'))
        if y is None or x is None:
            return False
        vertices.append((x, y))
    if len(vertices) < 3:
        return False
    inside = False
    x1, y1 = vertices[-1]
    for x2, y2 in vertices:
        cross = (lng-x1)*(y2-y1)-(lat-y1)*(x2-x1)
        if abs(cross) < 1e-10 and min(x1,x2) <= lng <= max(x1,x2) and min(y1,y2) <= lat <= max(y1,y2):
            return True
        if (y1 > lat) != (y2 > lat) and lng < (x2-x1)*(lat-y1)/(y2-y1)+x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def _resolve_ward_from_polygons(district, lat, lng, ward_polygons=None):
    districts = ward_polygons if isinstance(ward_polygons, dict) else {}
    polygons = districts.get(str(district or '').strip())
    if not isinstance(polygons, list):
        return ''

    for poly in polygons:
        obj = poly if isinstance(poly, dict) else {}
        ward = str(obj.get('ward') or '').strip()
        points = obj.get('points') if isinstance(obj.get('points'), list) else []
        if ward and _point_in_polygon(lat, lng, points):
            return ward
    return ''


def _resolve_village_from_svamitva(district: str, lat: float, lng: float, svamitva_maps=None):
    districts = svamitva_maps if isinstance(svamitva_maps, dict) else {}
    villages = districts.get(str(district or '').strip())
    if not isinstance(villages, list):
        return ''

    for item in villages:
        obj = item if isinstance(item, dict) else {}
        name = str(obj.get('village') or '').strip()
        points = obj.get('points') if isinstance(obj.get('points'), list) else []
        if name and _point_in_polygon(lat, lng, points):
            return name
    return ''


def _with_geo_telemetry(payload: dict, geo_telemetry: dict):
    out = dict(payload or {})
    out['geo_telemetry'] = dict(geo_telemetry or {})
    out['geo_confidence_basis'] = 'Heuristic routing signal; not independent location verification'
    out['boundary_verified'] = False
    return out


def _compute_geo_confidence(ward_source: str, lat, lng, geocode_success: bool, pin: str = '', village: str = '') -> float:
    source = str(ward_source or 'none').strip().lower()
    base_map = {
        'explicit': 1.0,
        'ward_polygon': 0.95,
        'text': 0.78,
        'map_grid': 0.70,
        'none': 0.20,
    }
    score = float(base_map.get(source, 0.25))
    if geocode_success:
        score += 0.05
    if str(pin or '').strip():
        score += 0.03
    if str(village or '').strip():
        score += 0.02
    if lat is None or lng is None:
        score = min(score, 0.35)
    return round(max(0.0, min(1.0, score)), 3)


def resolve_geo_ward_context(
    district: str,
    text: str = '',
    ward: str = '',
    lat=None,
    lng=None,
    pin: str = '',
    place_id: str = '',
    address: str = '',
    grid=None,
    ward_polygons=None,
    svamitva_maps=None,
    pin_index=None,
    google_maps_client=None,
):
    geo_telemetry = {
        'geocode_attempted': False,
        'geocode_success': False,
        'geocode_status': 'not_attempted',
        'geocode_latency_ms': 0.0,
        'geocode_model': '',
        'geocode_error': '',
        'geocode_provider': 'google_maps_geocoding_api',
    }

    explicit = str(ward or '').strip()
    if explicit:
        district_input = str(district or '').strip()
        explicit_payload = {
            'ward': explicit,
            'ward_source': 'explicit',
            'lat': _to_float(lat),
            'lng': _to_float(lng),
            'pin': str(pin or '').strip(),
            'village': '',
            'district': district_input,
        }
        explicit_payload['geo_confidence'] = 1.0
        explicit_payload['district_validation'] = {
            'input_district': district_input,
            'resolved_district': district_input,
            'evidence_district': '',
            'mismatch': False,
            'evidence_source': '',
        }
        return _with_geo_telemetry(explicit_payload, geo_telemetry)

    text_ward = _extract_ward_from_text(text=text, address=address)
    lat_f = _to_float(lat)
    lng_f = _to_float(lng)
    geocoded_district = ''

    if lat_f is None and google_maps_client and hasattr(google_maps_client, 'geocode_address'):
        query = (address or text or pin or district or '').strip()
        if query:
            geo_telemetry['geocode_attempted'] = True
            started = time.perf_counter()
            try:
                geo_res = google_maps_client.geocode_address(query)
                geo_telemetry['geocode_latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                geo_telemetry['geocode_status'] = str(geo_res.get('status') or 'UNKNOWN')
                geo_telemetry['geocode_model'] = str(geo_res.get('model') or '')
                geocoded_district = str(geo_res.get('district') or '').strip()
                if geo_res.get('success') and geo_res.get('lat') is not None:
                    lat_f = geo_res['lat']
                    lng_f = geo_res['lng']
                    geo_telemetry['geocode_success'] = True
            except Exception:
                geo_telemetry['geocode_latency_ms'] = round((time.perf_counter() - started) * 1000.0, 3)
                geo_telemetry['geocode_status'] = 'ERROR'
                geo_telemetry['geocode_error'] = 'geocode_request_failed'

    pin_lat, pin_lng, pin_district, pin_village = _resolve_pin_lat_lng(pin=pin, pin_index=pin_index)
    if lat_f is None and pin_lat is not None:
        lat_f = pin_lat
    if lng_f is None and pin_lng is not None:
        lng_f = pin_lng

    resolved_district = str(district or '').strip() or pin_district
    if not resolved_district:
        resolved_district = str(district or '').strip()

    district_input = str(district or '').strip()
    district_evidence = geocoded_district or pin_district
    district_mismatch = bool(district_input and district_evidence and district_input.lower() != district_evidence.lower())

    def _finalize(payload: dict):
        out = dict(payload or {})
        ward_source = str(out.get('ward_source') or 'none')
        out['geo_confidence'] = _compute_geo_confidence(
            ward_source=ward_source,
            lat=out.get('lat'),
            lng=out.get('lng'),
            geocode_success=bool(geo_telemetry.get('geocode_success')),
            pin=out.get('pin') or '',
            village=out.get('village') or '',
        )
        out['district_validation'] = {
            'input_district': district_input,
            'resolved_district': str(out.get('district') or ''),
            'evidence_district': district_evidence,
            'mismatch': district_mismatch,
            'evidence_source': 'google_geocode' if geocoded_district else ('pin_index' if pin_district else ''),
        }
        return _with_geo_telemetry(out, geo_telemetry)

    polygon_ward = ''
    village = pin_village
    if lat_f is not None and lng_f is not None:
        polygon_ward = _resolve_ward_from_polygons(resolved_district, lat_f, lng_f, ward_polygons=ward_polygons)
        if not village:
            village = _resolve_village_from_svamitva(resolved_district, lat_f, lng_f, svamitva_maps=svamitva_maps)

    if polygon_ward:
        return _finalize({
            'ward': polygon_ward,
            'ward_source': 'ward_polygon',
            'lat': lat_f,
            'lng': lng_f,
            'pin': str(pin or '').strip(),
            'village': village,
            'district': resolved_district,
        })

    if text_ward:
        return _finalize({
            'ward': text_ward,
            'ward_source': 'text',
            'lat': lat_f,
            'lng': lng_f,
            'pin': str(pin or '').strip(),
            'village': village,
            'district': resolved_district,
        })

    if lat_f is None or lng_f is None:
        return _finalize({
            'ward': '',
            'ward_source': 'none',
            'lat': lat_f,
            'lng': lng_f,
            'pin': str(pin or '').strip(),
            'village': village,
            'district': resolved_district,
        })

    grid_cfg = (grid or {}).get(str(resolved_district or '').strip(), {}) if isinstance(grid, dict) else {}
    lat_origin = float(grid_cfg.get('lat_origin', lat_f))
    lng_origin = float(grid_cfg.get('lng_origin', lng_f))
    cell_size = max(float(grid_cfg.get('cell_size', 0.02)), 0.001)
    prefix = str(grid_cfg.get('prefix', 'W'))

    y = int(abs((lat_f - lat_origin) / cell_size)) + 1
    x = int(abs((lng_f - lng_origin) / cell_size)) + 1
    return _finalize({
        'ward': f"{prefix}{y:02d}{x:02d}",
        'ward_source': 'map_grid',
        'lat': lat_f,
        'lng': lng_f,
        'pin': str(pin or '').strip(),
        'village': village,
        'district': resolved_district,
    })


def resolve_ward_from_map(district: str, text: str = '', ward: str = '', lat=None, lng=None, place_id: str = '', address: str = '', grid=None):
    context = resolve_geo_ward_context(
        district=district,
        text=text,
        ward=ward,
        lat=lat,
        lng=lng,
        place_id=place_id,
        address=address,
        grid=grid,
    )
    return context.get('ward') or '', context.get('ward_source') or 'none'
