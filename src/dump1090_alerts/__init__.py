"""Rule engine that watches dump1090 / readsb ``aircraft.json`` and fires notifications."""

from dump1090_alerts.config import Config, ConfigError, load_config
from dump1090_alerts.engine import AlertEngine, Firing
from dump1090_alerts.models import Alert

__all__ = [
    "Alert",
    "AlertEngine",
    "Config",
    "ConfigError",
    "Firing",
    "load_config",
]

__version__ = "0.1.0"
