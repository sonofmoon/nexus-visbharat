from __future__ import annotations

from typing import Dict, Any
import requests


class GoogleMapsPlatformClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError('GOOGLE_MAPS_API_KEY is required for live Google Maps Platform mode')
        self.api_key = api_key
        self.geocoding_base_url = 'https://maps.googleapis.com/maps/api/geocode/json'

    def geocode_address(self, address_or_place: str, components: str = 'country:IN') -> Dict[str, Any]:
        if not address_or_place or not address_or_place.strip():
            raise ValueError('Address or place query is empty')

        params = {
            'address': address_or_place.strip(),
            'key': self.api_key,
        }
        if components:
            params['components'] = components

        response = requests.get(self.geocoding_base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        status = data.get('status')
        if status != 'OK' or not data.get('results'):
            return {
                'success': False,
                'status': status or 'ZERO_RESULTS',
                'lat': None,
                'lng': None,
                'formatted_address': '',
                'district': '',
                'state': '',
                'place_id': '',
            }

        first = data['results'][0]
        geometry = first.get('geometry', {})
        location = geometry.get('location', {})
        lat = float(location.get('lat')) if location.get('lat') is not None else None
        lng = float(location.get('lng')) if location.get('lng') is not None else None

        district = ''
        state = ''
        for comp in first.get('address_components', []):
            types = comp.get('types', [])
            if 'administrative_area_level_2' in types or 'locality' in types:
                if not district:
                    district = comp.get('long_name', '')
            if 'administrative_area_level_1' in types:
                state = comp.get('long_name', '')

        return {
            'success': True,
            'status': status,
            'lat': lat,
            'lng': lng,
            'formatted_address': first.get('formatted_address', ''),
            'district': district,
            'state': state,
            'place_id': first.get('place_id', ''),
            'model': 'Google Maps Geocoding API',
        }
