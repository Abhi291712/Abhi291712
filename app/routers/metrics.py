"""The Prometheus scrape endpoint, ``GET /metrics``.

Returns all metrics in Prometheus' plain-text exposition format. It is unauthenticated, like
``/health``, because monitoring systems scrape it; in production it is usually reachable only
from inside the private network, or can be switched off with ``METRICS_ENABLED=false``.
"""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
