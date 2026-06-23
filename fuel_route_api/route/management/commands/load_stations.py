"""
Management command: load_stations

Reads fuel_stations.csv and loads all stations into the DB using a
bundled static US city coordinates lookup — no external API calls,
no rate limits, completes in seconds.

City coordinates source:
  https://github.com/kelvins/US-Cities-Database (public domain)
  Downloaded once at startup if not already cached locally.

Usage:
    python manage.py load_stations
    python manage.py load_stations --clear    # wipe before reload
"""

import csv
import io
import json
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from route.models import FuelStation


CITIES_URL = (
    "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
)
CITIES_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "us_cities_cache.csv"


class Command(BaseCommand):
    help = "Load fuel stations from CSV using static US city coordinates (instant, no API)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=str(settings.FUEL_CSV_PATH),
            help="Path to the fuel stations CSV.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing FuelStation rows before loading.",
        )

    def handle(self, *args, **options):
        # ── 1. Build city → (lat, lon) lookup ───────────────────────────────
        city_map = self._load_city_map()
        self.stdout.write(f"City lookup loaded: {len(city_map):,} US cities")

        # ── 2. Optionally clear existing data ───────────────────────────────
        if options["clear"]:
            deleted, _ = FuelStation.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing stations."))

        # ── 3. Read the fuel CSV ─────────────────────────────────────────────
        csv_path = Path(options["csv"])
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.stdout.write(f"Stations in CSV: {len(rows):,}")

        # ── 4. Build station objects ─────────────────────────────────────────
        existing_opis = set(FuelStation.objects.values_list("opis_id", flat=True))
        to_create: list[FuelStation] = []
        geocoded_ok = 0
        geocoded_fail = 0
        skipped_rows = 0

        for row in rows:
            try:
                opis_id = int(row["OPIS Truckstop ID"])
                city = row["City"].strip()
                state = row["State"].strip()
                retail_price = float(row["Retail Price"])
            except (KeyError, ValueError):
                skipped_rows += 1
                continue

            lat, lon = city_map.get((city.lower(), state.lower()), (None, None))
            if lat is not None:
                geocoded_ok += 1
            else:
                geocoded_fail += 1

            data = dict(
                opis_id=opis_id,
                name=row["Truckstop Name"].strip(),
                address=row["Address"].strip(),
                city=city,
                state=state,
                rack_id=int(row["Rack ID"]),
                retail_price=retail_price,
                latitude=lat,
                longitude=lon,
            )

            if opis_id in existing_opis:
                FuelStation.objects.filter(opis_id=opis_id).update(**data)
            else:
                existing_opis.add(opis_id)
                to_create.append(FuelStation(**data))

        # ── 5. Bulk insert ───────────────────────────────────────────────────
        if to_create:
            FuelStation.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)

        total_db = FuelStation.objects.count()
        geocoded_db = FuelStation.objects.filter(latitude__isnull=False).count()

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Done.\n"
            f"  Total in DB        : {total_db:,}\n"
            f"  With coordinates   : {geocoded_db:,} ({100*geocoded_db/max(total_db,1):.1f}%)\n"
            f"  Without coords     : {geocoded_fail:,} "
        ))

    def _load_city_map(self) -> dict:
        """
        Returns a dict of {(city_lower, state_code_lower): (lat, lon)}.

        Uses a local cache file to avoid re-downloading on every run.
        """
        if CITIES_CACHE.exists():
            raw = CITIES_CACHE.read_text(encoding="utf-8")
        else:
            resp = requests.get(CITIES_URL, timeout=15)
            resp.raise_for_status()
            raw = resp.text
            CITIES_CACHE.write_text(raw, encoding="utf-8")
            self.stdout.write(f"Cached to {CITIES_CACHE}")

        city_map = {}
        for row in csv.DictReader(io.StringIO(raw)):
            key = (row["CITY"].lower().strip(), row["STATE_CODE"].lower().strip())
            city_map[key] = (float(row["LATITUDE"]), float(row["LONGITUDE"]))

        return city_map