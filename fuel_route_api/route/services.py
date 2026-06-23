"""
Core services for the fuel-route API.

Flow:
  1. Geocode start + finish via ORS geocoding (1 call each).
  2. Fetch driving directions via ORS (1 call) → encoded polyline + distance.
  3. Decode the polyline into (lat, lon) points.
  4. Walk the points; every FUEL_STOP_INTERVAL miles find the cheapest
     FuelStation within STATION_SEARCH_RADIUS_MILES using a DB bounding-box
     query (no extra API calls).
  5. Calculate cost per leg (miles / mpg * price_per_gallon).
  6. Return structured result.

Total external API calls: 3  (2 geocode + 1 directions).
"""

import math
import requests
from django.conf import settings
from django.db.models import Avg

from .models import FuelStation


EARTH_RADIUS_MILES = 3_958.8
ORS_BASE = "https://api.openrouteservice.org"


# ─── geometry helpers ─────────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode a precision-5 Google-style encoded polyline → list of (lat, lon)."""
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += (~(result >> 1) if result & 1 else result >> 1)

        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lng += (~(result >> 1) if result & 1 else result >> 1)
        coords.append((lat / 1e5, lng / 1e5))
    return coords


def _cumulative_miles(points: list[tuple]) -> list[float]:
    """Running cumulative distance (miles) for each point in a polyline."""
    dists = [0.0]
    for i in range(1, len(points)):
        dists.append(dists[-1] + _haversine(*points[i - 1], *points[i]))
    return dists


# ─── ORS API calls ────────────────────────────────────────────────────────────

def _geocode(place: str) -> tuple[float, float]:
    """ORS geocoding → (lat, lon). Raises ValueError if nothing found."""
    resp = requests.get(
        f"{ORS_BASE}/geocode/search",
        params={
            "api_key": settings.ORS_API_KEY,
            "text": place,
            "boundary.country": "US",
            "size": 1,
        },
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise ValueError(f"Could not geocode '{place}' — try a more specific address.")
    lon, lat = features[0]["geometry"]["coordinates"]
    return lat, lon


def _get_driving_route(start_latlon: tuple, end_latlon: tuple) -> dict:
    """
    Single ORS directions call.
    Returns the full JSON response (routes[0] has geometry + summary).
    """
    resp = requests.post(
        f"{ORS_BASE}/v2/directions/driving-car",
        headers={
            "Authorization": settings.ORS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "coordinates": [
                [start_latlon[1], start_latlon[0]],   # ORS: [lon, lat]
                [end_latlon[1], end_latlon[0]],
            ],
            "geometry": True,
            "instructions": False,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# ─── station lookup ───────────────────────────────────────────────────────────

def _cheapest_nearby(lat: float, lon: float, radius_miles: float) -> FuelStation | None:
    """
    Cheapest FuelStation within radius_miles of (lat, lon).
    Bounding-box pre-filter → exact haversine check → return lowest price.
    """
    dlat = radius_miles / 69.0
    dlon = radius_miles / (69.0 * math.cos(math.radians(lat)))

    candidates = FuelStation.objects.filter(
        latitude__isnull=False,
        latitude__range=(lat - dlat, lat + dlat),
        longitude__range=(lon - dlon, lon + dlon),
    ).order_by("retail_price")

    for s in candidates:
        if _haversine(lat, lon, s.latitude, s.longitude) <= radius_miles:
            return s   # cheapest first
    return None


# ─── main entry point ─────────────────────────────────────────────────────────

def build_route(start: str, finish: str) -> dict:
    """
    Plan the fuel-optimal route from start to finish.

    Returns a dict with route details, fuel stops, and total cost.
    """
    if not settings.ORS_API_KEY:
        raise ValueError(
            "ORS_API_KEY is not set. Add it to your .env file. "
            "Get a free key at https://openrouteservice.org/dev/#/signup"
        )

    mpg = settings.VEHICLE_MPG
    tank_miles = settings.VEHICLE_TANK_MILES
    interval = settings.FUEL_STOP_INTERVAL
    radius = settings.STATION_SEARCH_RADIUS_MILES

    # ── 1 & 2. Geocode + route (3 API calls total) ──────────────────────────
    start_latlon = _geocode(start)
    end_latlon = _geocode(finish)
    route_data = _get_driving_route(start_latlon, end_latlon)


    route = route_data["routes"][0]
    encoded_poly = route["geometry"]
    total_miles = route["summary"]["distance"] / 1609.344


    # ── 3. Decode polyline ───────────────────────────────────────────────────
    points = _decode_polyline(encoded_poly)
    cum = _cumulative_miles(points)

    # ── 4. Place fuel stops ──────────────────────────────────────────────────
    stops_raw: list[dict] = []
    seen_ids: set[int] = set()
    next_check = interval   # first stop target in miles

    for i, (lat, lon) in enumerate(points):
        if cum[i] < next_check:
            continue
        if cum[i] >= total_miles - 20:
            break   # don't stop in the last 20 miles

        station = _cheapest_nearby(lat, lon, radius)
        if station and station.id not in seen_ids:
            seen_ids.add(station.id)
            stops_raw.append({
                "lat": lat,
                "lon": lon,
                "miles_from_start": cum[i],
                "station": station,
            })
            next_check = cum[i] + interval
        elif not station:
            next_check = cum[i] + 20   # nudge forward and try again

    # ── 5. Cost per leg ──────────────────────────────────────────────────────
    # Leg i covers miles from mile-marker[i-1] to mile-marker[i+1]
    # (i.e. the driver fills up enough to reach the NEXT stop or destination)
    mile_markers = [0.0] + [s["miles_from_start"] for s in stops_raw] + [total_miles]

    fuel_stops_out = []
    total_cost = 0.0

    for i, raw in enumerate(stops_raw):
        # gallons needed to cover from the previous stop to THIS stop,
        # plus the stretch to the next stop (fill tank here for the leg ahead)
        leg_start = mile_markers[i]          # where driver was after last fill
        leg_end = mile_markers[i + 2]        # where driver will be after next fill (or end)
        gallons = (leg_end - leg_start) / mpg
        cost = gallons * raw["station"].retail_price
        total_cost += cost
        fuel_stops_out.append({
            "stop_number": i + 1,
            "station_name": raw["station"].name,
            "address": raw["station"].address,
            "city": raw["station"].city,
            "state": raw["station"].state,
            "retail_price_per_gallon": round(raw["station"].retail_price, 3),
            "latitude": raw["station"].latitude,
            "longitude": raw["station"].longitude,
            "miles_from_start": round(raw["miles_from_start"], 1),
            "gallons_purchased": round(gallons, 2),
            "cost_at_stop": round(cost, 2),
        })

    # Edge case: route fits in one tank — no stops needed
    if not stops_raw:
        avg_price = FuelStation.objects.aggregate(avg=Avg("retail_price"))["avg"] or 3.5
        total_cost = (total_miles / mpg) * avg_price

    total_gallons = total_miles / mpg

    # ── 6. Map URL ───────────────────────────────────────────────────────────
    map_url = (
        f"https://maps.openrouteservice.org/directions?"
        f"n1={start_latlon[0]}&e1={start_latlon[1]}"
        f"&n2={end_latlon[0]}&e2={end_latlon[1]}"
        f"&b=0&c=0&k1=en-US&k2=km"
    )

    return {
        "start": start,
        "finish": finish,
        "start_coordinates": {"lat": round(start_latlon[0], 5), "lon": round(start_latlon[1], 5)},
        "finish_coordinates": {"lat": round(end_latlon[0], 5), "lon": round(end_latlon[1], 5)},
        "total_distance_miles": round(total_miles, 1),
        "total_gallons_used": round(total_gallons, 2),
        "total_fuel_cost_usd": round(total_cost, 2),
        "vehicle_mpg": mpg,
        "vehicle_max_range_miles": tank_miles,
        "fuel_stops_count": len(fuel_stops_out),
        "fuel_stops": fuel_stops_out,
        "map_url": map_url,
        "encoded_polyline": encoded_poly,
    }
