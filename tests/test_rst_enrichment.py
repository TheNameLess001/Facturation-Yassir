from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.config import Settings
from src.google.models import DriveFile
from src.ingestion.admin_earnings_models import NormalizedAdminEarningsRow
from src.ingestion.payment_scope_models import (
    EligibilityRecord,
    EligibilityResult,
    PaymentScopeSnapshot,
)
from src.models.enums import EligibilityState, IngestionStatus
from src.restaurants.registry import RestaurantRegistryService
from src.restaurants.rst_enrichment import RSTEnrichmentService


class Drive:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def download_file(self, file_id: str) -> bytes:
        return self.content


def source() -> DriveFile:
    return DriveFile(
        file_id="rst",
        name="RST List.csv",
        mime_type="text/csv",
        modified_time=datetime(2026, 8, 10, tzinfo=UTC),
        size=100,
    )


def content(rows: list[list[object]]) -> bytes:
    return (
        pd.DataFrame(
            rows,
            columns=[
                "Restaurant ID",
                "Restaurant Name",
                "Chain",
                "ICE",
                "RIB",
                "Email",
                "Commission",
            ],
        )
        .to_csv(index=False)
        .encode()
    )


def order(restaurant_id: str) -> NormalizedAdminEarningsRow:
    return NormalizedAdminEarningsRow(
        order_id=f"O-{restaurant_id}",
        restaurant_id=restaurant_id,
        restaurant_name="Source Name",
        order_date=datetime(2026, 8, 12, tzinfo=UTC),
        gross_amount=Decimal(100),
        operational_status="DELIVERED",
        source_file_id="earn",
        source_filename="earn.csv",
        source_row_number=2,
    )


def eligibility(*restaurant_ids: str) -> EligibilityResult:
    snapshot = PaymentScopeSnapshot(
        snapshot_id="snap",
        period_id="2026-08-P1",
        drive_file_id="scope",
        filename="scope.csv",
        drive_modified_at=datetime(2026, 8, 10, tzinfo=UTC),
        content_hash="hash",
        snapshot_at=datetime(2026, 8, 10, tzinfo=UTC),
        restaurant_ids=restaurant_ids,
    )
    return EligibilityResult(
        status=IngestionStatus.SUCCESS,
        period_id="2026-08-P1",
        scope_snapshot=snapshot,
        eligible_orders=tuple(
            EligibilityRecord(
                order=order(item),
                state=EligibilityState.ELIGIBLE,
                reason="RESTAURANT_ID_IN_PAYMENT_SCOPE",
            )
            for item in restaurant_ids
        ),
    )


def test_rst_parsing_and_chain_mapping() -> None:
    data = content(
        [
            [
                "R-1",
                "Chain Store",
                "Pizza Group",
                "ICE1",
                "RIB1",
                "one@example.com",
                "20%",
            ],
            ["R-2", "Standalone", "", "ICE2", "RIB2", "two@example.com", "0.15"],
        ]
    )
    result = RSTEnrichmentService(Drive(data), Settings(_env_file=None)).ingest_master(
        source()
    )
    assert result.status == IngestionStatus.SUCCESS
    assert result.restaurants[0].commission_rate == Decimal("0.2")
    assert result.restaurants[1].commission_rate == Decimal("0.15")
    assert result.restaurants[1].chain is None


def test_enrichment_applies_only_after_payment_scope_eligibility() -> None:
    data = content(
        [
            ["R-1", "Eligible", "", "ICE1", "RIB1", "one@example.com", "20"],
            ["R-9", "RST Only", "", "ICE9", "RIB9", "nine@example.com", "20"],
        ]
    )
    service = RSTEnrichmentService(Drive(data), Settings(_env_file=None))
    result = service.enrich(eligibility("R-1"), service.ingest_master(source()))
    assert result.status == IngestionStatus.SUCCESS
    assert [item.restaurant_id for item in result.restaurants] == ["R-1"]
    assert all(item.restaurant.restaurant_id == "R-1" for item in result.records)  # type: ignore[union-attr]


def test_eligible_restaurant_missing_from_rst_is_blocking() -> None:
    service = RSTEnrichmentService(
        Drive(content([["R-1", "One", "", "ICE", "RIB", "one@example.com", "20"]])),
        Settings(_env_file=None),
    )
    result = service.enrich(eligibility("R-2"), service.ingest_master(source()))
    assert result.status == IngestionStatus.BLOCKED
    assert result.missing_restaurant_ids == ("R-2",)


def test_conflicting_rst_identity_is_blocking() -> None:
    data = content(
        [
            ["R-1", "One", "", "ICE", "RIB", "one@example.com", "20"],
            ["R-1", "Different", "", "ICE", "RIB", "one@example.com", "20"],
        ]
    )
    result = RSTEnrichmentService(Drive(data), Settings(_env_file=None)).ingest_master(
        source()
    )
    assert result.status == IngestionStatus.BLOCKED
    assert result.restaurants == ()


def test_restaurant_registry_chain_and_standalone_paths(tmp_path) -> None:
    parsed = RSTEnrichmentService(
        Drive(
            content(
                [
                    ["R-1", "One", "Group", "ICE", "RIB", "one@example.com", "20"],
                    ["R-2", "Two", "", "ICE", "RIB", "two@example.com", "20"],
                ]
            )
        ),
        Settings(_env_file=None),
    ).ingest_master(source())
    registry = RestaurantRegistryService(tmp_path / "restaurants.sqlite3")
    registry.upsert(parsed.restaurants, parsed.content_hash or "hash")
    assert registry.folder_path("R-1") == ("CHAINS", "Group", "R-1_One")
    assert registry.folder_path("R-2") == ("STANDALONE", "R-2_Two")
    registry.upsert(parsed.restaurants, parsed.content_hash or "hash")
    assert len(registry.list_all()) == 2
