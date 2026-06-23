# Fuel Route API

A Django REST API that plans a driving route between two locations in the United States and suggests where to refuel along the way. The goal is to **minimize total fuel cost** by picking the cheapest stations near the route at sensible intervals.

You send a start point and a finish point (city names or addresses). The API returns the driving route, recommended fuel stops, gallons purchased at each stop, and the estimated total fuel bill.

## How it works

1. **Geocode** the start and finish locations using [OpenRouteService](https://openrouteservice.org/) (US only).
2. **Fetch the driving route** in a single directions request (encoded polyline + total distance).
3. **Walk the route** every ~450 miles (vehicle range is 500 miles, leaving a safety buffer).
4. At each checkpoint, find the **cheapest fuel station within 50 miles** of the route using a local database lookup (bounding box + haversine distance).
5. **Calculate cost** assuming **10 mpg** and each station's retail price.

Fuel station data comes from `fuel_stations.csv`. Stations are loaded into SQLite with coordinates derived from a bundled US cities lookup — no per-station geocoding API calls at load time.

## Requirements

- Python 3.10+
- An [OpenRouteService API key](https://openrouteservice.org/dev/#/signup) (free tier: 2,000 requests/day)
- Each route request uses 3 ORS calls (2 geocode + 1 directions)

## Run locally

### 1. Clone and enter the project

```bash
cd fuel_route_api
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment (optional)

```bash
cp .env.example .env
```

Edit `.env` if you want to use your own OpenRouteService key or Django settings. The API key is also configured in `config/settings.py` as `ORS_API_KEY`.

### 4. Set up the database

```bash
python manage.py migrate
python manage.py load_stations
```

`load_stations` reads `fuel_stations.csv` and populates the database. It completes in seconds using the cached `us_cities_cache.csv` file. Use `--clear` to wipe existing stations before reloading:

```bash
python manage.py load_stations --clear
```

### 5. Start the development server

```bash
python manage.py runserver
```

The API is available at `http://127.0.0.1:8000/api/route/`.

## Run with Docker

The Docker image runs **nginx** on port 80, proxying to **uWSGI** serving the Django app. On startup it automatically runs migrations and loads fuel stations.

### Build the image

```bash
docker build -t fuel-route-api .
```

### Run the container

```bash
docker run -p 8080:80 fuel-route-api
```

The API is available at `http://localhost:8080/api/route/`.

The first startup may take a minute while stations are loaded into the database.

### Optional environment variables

Pass these when starting the container if needed:

| Variable | Description |
|---|---|
| `ORS_API_KEY` | OpenRouteService API key (if overridden in settings) |
| `DJANGO_SUPERUSER_USERNAME` | Creates a Django admin user on startup |
| `DJANGO_SUPERUSER_PASSWORD` | Password for the admin user |

Example with an env file:

```bash
docker run -p 8080:80 --env-file .env fuel-route-api
```

Example with inline admin credentials:

```bash
docker run -p 8080:80 \
  -e DJANGO_SUPERUSER_USERNAME=admin \
  -e DJANGO_SUPERUSER_PASSWORD=changeme \
  fuel-route-api
```

Admin panel: `http://localhost:8080/admin/`

## API usage

### `POST /api/route/`

**Request:**

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

Both fields are required. Use US city names or addresses.

**Example with curl:**

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "New York, NY", "finish": "Los Angeles, CA"}'
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
  "map_url": "https://www.google.com/maps/dir/?api=1&...",
  "encoded_polyline": "..."
}
```

### Error responses

| Status | Meaning |
|---|---|
| `400` | Missing or invalid `start`/`finish`, or geocoding failed |
| `502` | OpenRouteService API error or network failure |
| `503` | No fuel stations loaded — run `python manage.py load_stations` |


## Stack

- Django 5.x + Django REST Framework
- SQLite (swap to Postgres in `config/settings.py` for production)
- OpenRouteService (geocoding + directions)
- nginx + uWSGI (Docker deployment)
