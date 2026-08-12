import json
import logging
from datetime import UTC, datetime


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in (
            "auth_mode",
            "root_folder_id",
            "source_type",
            "change_state",
            "files_found",
            "rows_read",
            "valid_records",
            "restaurant_count",
            "eligible_orders",
            "out_of_scope_orders",
            "period",
            "status",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger("src")
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.INFO)
