from __future__ import annotations

import pandas as pd

from src.restaurants.registry_models import (
    DataQualityStatus,
    MappingStatus,
)
from src.restaurants.scope_registry import (
    RestaurantRegistryBuilder,
    normalize_restaurant_name,
)
from src.restaurants.source_reader import RestaurantSourceReader


def profiles(scope: pd.DataFrame, rst: pd.DataFrame):
    return (
        RestaurantSourceReader.profile_invoice_frame(scope),
        RestaurantSourceReader.profile_rst_frame(rst),
    )


def build(
    scope: pd.DataFrame,
    rst: pd.DataFrame,
    *,
    aliases: dict[str, str] | None = None,
    order_counts: dict[str, int] | None = None,
):
    scope_profile, rst_profile = profiles(scope, rst)
    return RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=scope_profile,
        rst_profile=rst_profile,
        alias_map=aliases,
        canonical_order_counts=order_counts,
    )


def complete_rst(**updates) -> dict[str, object]:
    row: dict[str, object] = {
        "Restaurant ID": "123",
        "Restaurant Name": "O'Tacos Ziraoui",
        "Parent": "O'Tacos",
        "Main City": "Casablanca",
        "Sub City": "Centre",
        "Address": "1 Main Street",
        "Phone": "+212000000000",
        "Email": "billing@example.test",
        "Commission %": "0.22",
        "Legal Entity": "Example SARL",
        "ICE": "001",
        "IF": "002",
        "RC": "003",
        "RIB": "000011112222333344445555",
        "Bank": "Example Bank",
        "Finance Email": "finance@example.test",
        "AM": "Owner",
    }
    row.update(updates)
    return row


def test_invoice_scope_and_rst_schema_profiling() -> None:
    scope = pd.DataFrame(
        [
            {"Column 1": "A", "Restaurant ID": "1", "CITY": "Rabat"},
            {"Column 1": "A", "Restaurant ID": "1", "CITY": "Rabat"},
            {"Column 1": None, "Restaurant ID": None, "CITY": None},
        ]
    )
    rst = pd.DataFrame([complete_rst()])
    scope_profile, rst_profile = profiles(scope, rst)
    assert scope_profile.active.columns == ("Column 1", "Restaurant ID", "CITY")
    assert scope_profile.active.row_count == 2
    assert scope_profile.active.blank_rows == 1
    assert scope_profile.active.duplicate_rows == 1
    assert rst_profile.row_count == 1
    assert rst_profile.field_types["Restaurant ID"] == "string"


def test_exact_id_normalization_enrichment_chain_and_order_join() -> None:
    scope = pd.DataFrame(
        [{"Column 1": "O'Tacos Ziraoui", "Restaurant ID": 123.0, "CITY": "Casa"}]
    )
    result = build(scope, pd.DataFrame([complete_rst()]), order_counts={"123": 7})
    restaurant = result.restaurants[0]
    assert restaurant.restaurant_id == "123"
    assert restaurant.restaurant_name == "O'Tacos Ziraoui"
    assert restaurant.mapping_status == MappingStatus.MATCHED_BY_ID
    assert restaurant.chain == "O'Tacos"
    assert restaurant.is_chain is True
    assert restaurant.legal_entity == "Example SARL"
    assert restaurant.admin_orders_available is True
    assert restaurant.canonical_order_count == 7
    assert restaurant.data_quality_status == DataQualityStatus.HEALTHY


def test_exact_unique_name_mapping_without_fabricating_id() -> None:
    scope = pd.DataFrame([{"Column 1": "  o’tacos   ziraoui ", "Restaurant ID": None}])
    result = build(scope, pd.DataFrame([complete_rst()]))
    assert normalize_restaurant_name("O’TACOS  Ziraoui") == "o'tacos ziraoui"
    assert result.restaurants[0].mapping_status == MappingStatus.MATCHED_BY_EXACT_NAME
    assert result.restaurants[0].restaurant_id == "123"
    assert result.scope_rows_without_restaurant_id == 1


def test_controlled_alias_mapping() -> None:
    scope = pd.DataFrame([{"Column 1": "Legacy Store", "Restaurant ID": None}])
    result = build(
        scope,
        pd.DataFrame([complete_rst()]),
        aliases={"Legacy Store": "123"},
    )
    assert result.restaurants[0].mapping_status == MappingStatus.MATCHED_BY_ALIAS


def test_ambiguous_name_mapping_is_blocking() -> None:
    scope = pd.DataFrame([{"Column 1": "Same Name"}])
    rst = pd.DataFrame(
        [
            complete_rst(**{"Restaurant ID": "1", "Restaurant Name": "Same Name"}),
            complete_rst(**{"Restaurant ID": "2", "Restaurant Name": "same name"}),
        ]
    )
    result = build(scope, rst)
    assert result.restaurants[0].mapping_status == MappingStatus.AMBIGUOUS
    assert result.restaurants[0].data_quality_status == DataQualityStatus.BLOCKING
    assert result.issue_count("AMBIGUOUS_RESTAURANT_MAPPING") == 1


def test_conflicting_rst_rows_for_same_id_are_ambiguous() -> None:
    scope = pd.DataFrame([{"Column 1": "Store", "Restaurant ID": "123"}])
    rst = pd.DataFrame(
        [
            complete_rst(**{"Restaurant Name": "Store", "Main City": "Rabat"}),
            complete_rst(**{"Restaurant Name": "Store", "Main City": "Casablanca"}),
        ]
    )
    result = build(scope, rst)
    assert result.restaurants[0].mapping_status == MappingStatus.AMBIGUOUS
    assert result.issue_count("AMBIGUOUS_RESTAURANT_MAPPING") == 1


def test_unmatched_scope_restaurant_is_blocking() -> None:
    scope = pd.DataFrame([{"Column 1": "Unknown", "Restaurant ID": "missing"}])
    result = build(scope, pd.DataFrame([complete_rst()]))
    assert result.restaurants[0].mapping_status == MappingStatus.UNMATCHED
    assert result.issue_count("UNMATCHED_SCOPE_RESTAURANT") == 1


def test_duplicate_and_conflicting_scope_rows() -> None:
    duplicate_scope = pd.DataFrame(
        [
            {"Column 1": "O'Tacos Ziraoui", "Restaurant ID": "123", "CITY": "Rabat"},
            {"Column 1": "O'Tacos Ziraoui", "Restaurant ID": "123", "CITY": "Rabat"},
        ]
    )
    duplicate = build(duplicate_scope, pd.DataFrame([complete_rst()]))
    assert len(duplicate.restaurants) == 1
    assert duplicate.restaurants[0].mapping_status == MappingStatus.DUPLICATE_SCOPE
    assert duplicate.restaurants[0].data_quality_status == DataQualityStatus.WARNING
    assert "DUPLICATE_SCOPE_ROW" in duplicate.restaurants[0].issue_codes
    assert duplicate.issue_count("DUPLICATE_SCOPE_ROW") == 1

    conflicting_scope = duplicate_scope.copy()
    conflicting_scope.loc[1, "Column 1"] = "Different Store"
    conflicting = build(conflicting_scope, pd.DataFrame([complete_rst()]))
    assert conflicting.restaurants[0].mapping_status == MappingStatus.CONFLICTING_SCOPE
    assert conflicting.restaurants[0].data_quality_status == DataQualityStatus.BLOCKING
    assert "CONFLICTING_SCOPE_ROW" in conflicting.restaurants[0].issue_codes
    assert conflicting.issue_count("CONFLICTING_SCOPE_ROW") == 1


def test_duplicate_scope_status_does_not_hide_unmatched_identity() -> None:
    scope = pd.DataFrame(
        [
            {"Column 1": "Missing", "Restaurant ID": "missing"},
            {"Column 1": "Missing", "Restaurant ID": "missing"},
        ]
    )
    result = build(scope, pd.DataFrame([complete_rst()]))
    restaurant = result.restaurants[0]
    assert restaurant.mapping_status == MappingStatus.DUPLICATE_SCOPE
    assert restaurant.data_quality_status == DataQualityStatus.BLOCKING
    assert set(restaurant.issue_codes) == {
        "DUPLICATE_SCOPE_ROW",
        "UNMATCHED_SCOPE_RESTAURANT",
    }


def test_standalone_and_missing_quality_fields() -> None:
    scope = pd.DataFrame([{"Column 1": "Standalone", "Restaurant ID": "123"}])
    rst = pd.DataFrame(
        [
            complete_rst(
                **{
                    "Restaurant Name": "Standalone",
                    "Parent": None,
                    "Email": None,
                    "RIB": None,
                    "Legal Entity": None,
                    "ICE": None,
                    "Commission %": None,
                }
            )
        ]
    )
    result = build(scope, rst)
    restaurant = result.restaurants[0]
    assert restaurant.is_chain is False
    assert restaurant.admin_orders_available is False
    assert restaurant.data_quality_status == DataQualityStatus.WARNING
    assert set(restaurant.issue_codes) >= {
        "MISSING_EMAIL",
        "MISSING_RIB",
        "MISSING_LEGAL_ENTITY",
        "MISSING_ICE",
        "MISSING_COMMISSION",
    }


def test_invoice_scope_commission_is_retained_when_rst_commission_is_missing() -> None:
    scope = pd.DataFrame(
        [{"Column 1": "O'Tacos Ziraoui", "Restaurant ID": "123", "Commission": "0.22"}]
    )
    rst = pd.DataFrame([complete_rst(**{"Commission %": None})])
    result = build(scope, rst)
    assert str(result.restaurants[0].commission_rate) == "0.22"
    assert "MISSING_COMMISSION" not in result.restaurants[0].issue_codes


def test_scope_explicit_false_field_is_not_in_registry() -> None:
    scope = pd.DataFrame(
        [
            {"Column 1": "O'Tacos Ziraoui", "Restaurant ID": "123", "Active": "yes"},
            {"Column 1": "Excluded", "Restaurant ID": "456", "Active": "false"},
        ]
    )
    result = build(scope, pd.DataFrame([complete_rst()]))
    assert result.scope_rows == 1
    assert len(result.restaurants) == 1


def test_finance_tracking_is_not_a_registry_input() -> None:
    scope = pd.DataFrame([{"Column 1": "O'Tacos Ziraoui", "Restaurant ID": "123"}])
    result = build(scope, pd.DataFrame([complete_rst()]))
    dumped = result.model_dump()
    assert "finance_tracking" not in dumped
    assert "payment_scope" not in dumped
