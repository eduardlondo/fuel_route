# Fuel Route API

A Django REST API that calculates the optimal (cost-effective) fuel stops for a road trip across the USA.

## How it works

1. Geocodes `start` and `finish` addresses via **OpenRouteService** (free, 2000 req/day).
2. Fetches the driving route in **one API call** (encoded polyline + total distance).
3. Walks the decoded route every **450 miles** (vehicle has 500-mile range) and finds the **cheapest fuel station** within 50 miles of the route using a bounding-box + haversine pre-filter on the local DB.
4. Returns total cost assuming **10 mpg**.

## Stack

- Django 5.x + Django REST Framework
- SQLite (easily swappable to Postgres)
- OpenRouteService API (free tier)
- Nominatim / OpenStreetMap (geocoding stations, no key required)

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and add your ORS_API_KEY
# Get one free at https://openrouteservice.org/dev/#/signup
```

### 3. Run migrations

```bash
python manage.py migrate
```

### 4. Load fuel stations

This geocodes all stations from the CSV using OpenStreetMap (free, no key).
Takes ~30–60 min for all 8,151 rows due to Nominatim's 1 req/sec rate limit.
Use `--limit 500` for a quick demo with stations across many states.

```bash
# Full load (recommended for production demo):
python manage.py load_stations

# Quick load for testing (~10 min):
python manage.py load_stations --limit 500
```

### 5. Run the server

```bash
python manage.py runserver
```

## API Usage

### `POST /api/route/`

**Request:**
```json
{
    "start": "New York, NY",
    "finish": "Los Angeles, CA"
}
```

**Response:**
```json
{
    "start": "New York, NY",
    "finish": "Los Angeles, CA",
    "start_coordinates": {"lat": 40.7128, "lon": -74.0060},
    "finish_coordinates": {"lat": 34.0522, "lon": -118.2437},
    "total_distance_miles": 2794.5,
    "total_gallons_used": 279.45,
    "total_fuel_cost_usd": 896.23,
    "vehicle_mpg": 10,
    "vehicle_max_range_miles": 500,
    "fuel_stops_count": 6,
    "fuel_stops": [
        {
            "stop_number": 1,
            "station_name": "PILOT TRAVEL CENTER #42",
            "address": "I-76, EXIT 53",
            "city": "Youngstown",
            "state": "OH",
            "retail_price_per_gallon": 2.899,
            "latitude": 41.099,
            "longitude": -80.649,
            "miles_from_start": 447.2,
            "gallons_purchased": 44.72,
            "cost_at_stop": 129.67
        }
    ],
    "map_url": "https://maps.openrouteservice.org/directions?...",
    "encoded_polyline": "..."
}
```

## Architecture notes

- **Single ORS API call**: The directions call returns full geometry; geocoding the origin/destination are lightweight calls.
- **Station lookup is pure DB**: No external calls during route planning — the bounding-box query is O(log n) with the DB index on `(latitude, longitude, retail_price)`.
- **Nominatim geocoding** runs once at load time, not at query time.

## ORS Free Tier

- 2,000 requests/day on the free plan.
- This API uses: 1 geocode call (start) + 1 geocode call (finish) + 1 directions call = **3 calls per request**.
