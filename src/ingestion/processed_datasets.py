from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from src.models.domain import Order, RestaurantSettlement


class ProcessedDatasetService:
    """Atomic period datasets for UI reads; monetary values remain decimal strings."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def write_period(
        self,
        period_id: str,
        orders: tuple[Order, ...],
        summaries: tuple[RestaurantSettlement, ...],
        source_snapshot_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        period_root = self.root / period_id[:4] / period_id
        period_root.mkdir(parents=True, exist_ok=True)
        order_rows = [
            self._order_row(item)
            for item in orders
            if item.settlement_period == period_id
        ]
        summary_rows = [self._summary_row(item) for item in summaries]
        orders_path = period_root / "orders.parquet"
        summary_path = period_root / "restaurant_summary.parquet"
        self._atomic_parquet(pd.DataFrame(order_rows), orders_path)
        self._atomic_parquet(pd.DataFrame(summary_rows), summary_path)
        manifest = {
            "period_id": period_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "orders": len(order_rows),
            "restaurants": len(summary_rows),
            "source_snapshot_ids": list(source_snapshot_ids),
            "orders_sha256": self._hash(orders_path),
            "restaurant_summary_sha256": self._hash(summary_path),
        }
        self._atomic_text(period_root / "manifest.json", json.dumps(manifest, indent=2))
        return manifest

    def read_orders(self, period_id: str) -> pd.DataFrame:
        return pd.read_parquet(self.root / period_id[:4] / period_id / "orders.parquet")

    def read_summaries(self, period_id: str) -> pd.DataFrame:
        return pd.read_parquet(
            self.root / period_id[:4] / period_id / "restaurant_summary.parquet"
        )

    @staticmethod
    def _order_row(order: Order) -> dict[str, object]:
        result = order.model_dump(mode="json")
        result["gross_amount"] = str(order.gross_amount)
        return result

    @staticmethod
    def _summary_row(summary: RestaurantSettlement) -> dict[str, object]:
        result = summary.model_dump(mode="json")
        for field in ("gross_sales", "commission", "adjustments", "net_payable"):
            result[field] = str(getattr(summary, field))
        return result

    @staticmethod
    def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
        with NamedTemporaryFile(
            dir=destination.parent, suffix=".parquet", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            frame.to_parquet(temporary, index=False)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_text(destination: Path, content: str) -> None:
        with NamedTemporaryFile(
            dir=destination.parent,
            suffix=".json",
            mode="w",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
