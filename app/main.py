import logging

from app.api.routes import app
from app.core.config import get_settings

settings = get_settings()
resolved_level = getattr(logging, settings.log_level.upper(), logging.INFO)
logging.basicConfig(
    level=resolved_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

__all__ = ["app"]
