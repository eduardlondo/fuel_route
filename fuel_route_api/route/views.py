import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .services import build_route
from .models import FuelStation


class RouteView(APIView):
    """
    POST /api/route/

    Body (JSON):
        {
            "start": "New York, NY",
            "finish": "Los Angeles, CA"
        }

    Returns the optimal fuel route with cheapest stops and total cost.
    """

    def post(self, request):
        start = request.data.get("start", "").strip()
        finish = request.data.get("finish", "").strip()

        if not start or not finish:
            return Response(
                {"error": "Both 'start' and 'finish' fields are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Quick sanity check: do we have any stations loaded?
        station_count = FuelStation.objects.filter(latitude__isnull=False).count()
        if station_count == 0:
            return Response(
                {
                    "error": (
                        "No geocoded fuel stations found in the database. "
                        "Run: python manage.py load_stations"
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            result = build_route(start, finish)
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except requests.exceptions.HTTPError as exc:
            return Response(
                {"error": f"Routing API error: {exc.response.text}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except requests.exceptions.RequestException as exc:
            return Response(
                {"error": f"Network error calling routing API: {str(exc)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
