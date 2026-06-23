from django.db import models


class FuelStation(models.Model):
    """Fuel station loaded from the OPIS CSV."""

    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=10, db_index=True)
    rack_id = models.IntegerField()
    retail_price = models.FloatField()

    # Geocoded coordinates (populated by load_stations management command)
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["retail_price"]
        indexes = [
            # Composite index for fast bounding-box + price queries
            models.Index(fields=["latitude", "longitude", "retail_price"], name="idx_latlon_price"),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) — ${self.retail_price:.3f}"
