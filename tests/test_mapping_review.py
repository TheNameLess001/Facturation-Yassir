from __future__ import annotations

import pandas as pd

from src.restaurants.mapping_review import CandidateRankingService
from src.restaurants.registry_models import (
    ConflictInterpretation,
    CorrectionConfidence,
    MappingStatus,
    NoIdClassification,
    ScopeConflictReason,
    ScopeSourceRow,
    SuggestionStrength,
)
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader


def rst_row(
    restaurant_id: str,
    name: str,
    *,
    city: str = "Casablanca",
    area: str = "Centre",
    chain: str | None = None,
    legal: str | None = "Example SARL",
    rib: str | None = "001122",
) -> dict[str, object]:
    return {
        "Restaurant ID": restaurant_id,
        "Restaurant Name": name,
        "Main City": city,
        "Sub City": area,
        "Parent": chain,
        "Store Type": "Restaurant",
        "Restaurant Status": "ACTIVE",
        "Commission %": "0.20",
        "Email": "billing@example.test",
        "Address": "1 Main Street",
        "Legal Entity": legal,
        "ICE": "001",
        "RIB": rib,
    }


def build(scope_rows, rst_rows, *, orders=None, order_names=None):
    scope = pd.DataFrame(scope_rows)
    if "Restaurant ID" not in scope.columns:
        scope["Restaurant ID"] = None
    if "Commission" not in scope.columns:
        scope["Commission"] = "0.20"
    rst = pd.DataFrame(rst_rows)
    return RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
        canonical_order_counts=orders or {},
        canonical_order_names=order_names or {},
    )


def test_candidate_ranking_never_auto_matches_typo() -> None:
    result = build(
        [{"Column 1": "OTacos Ziraou", "CITY": "Casablanca"}],
        [rst_row("123", "O'Tacos Ziraoui", chain="O'Tacos")],
    )
    restaurant = result.restaurants[0]
    case = result.mapping_cases[0]
    assert restaurant.mapping_status == MappingStatus.UNMATCHED
    assert restaurant.readiness.identity_ready is False
    assert case.candidates[0].restaurant_id == "123"
    assert case.suggestion_strength in {
        SuggestionStrength.STRONG_SINGLE_CANDIDATE,
        SuggestionStrength.WEAK_SUGGESTION,
    }


def test_ambiguous_exact_name_remains_ambiguous_with_candidates() -> None:
    result = build(
        [{"Column 1": "Same Store", "CITY": "Casablanca"}],
        [
            rst_row("1", "Same Store", city="Casablanca"),
            rst_row("2", "Same Store", city="Rabat"),
        ],
    )
    assert result.restaurants[0].mapping_status == MappingStatus.AMBIGUOUS
    assert len(result.mapping_cases[0].candidates) == 2
    assert result.mapping_cases[0].candidates[0].restaurant_id == "1"


def test_city_is_advisory_ranking_signal() -> None:
    scope = ScopeSourceRow(
        source_row=2,
        restaurant_name="Restaurant X",
        city="Casablanca",
    )
    records = (
        {
            "restaurant_id": "rabat",
            "restaurant_name": "Restaurant X Rabat",
            "city": "Rabat",
        },
        {
            "restaurant_id": "casa",
            "restaurant_name": "Restaurant X Casablanca",
            "city": "Casablanca",
        },
    )
    candidates = CandidateRankingService().rank(scope, records, {})
    assert candidates[0].restaurant_id == "casa"
    assert candidates[0].same_city is True
    assert "SAME CITY" in candidates[0].similarity_indicators


def test_chain_signal_is_visible() -> None:
    scope = ScopeSourceRow(source_row=2, restaurant_name="O'Tacos Ziraoui")
    records = (
        {
            "restaurant_id": "123",
            "restaurant_name": "Ziraoui",
            "chain": "O'Tacos",
        },
    )
    candidate = CandidateRankingService().rank(scope, records, {})[0]
    assert candidate.chain_signal is True
    assert "CHAIN/BRAND TOKEN" in candidate.similarity_indicators


def test_admin_orders_are_supporting_signal_not_mapping_decision() -> None:
    result = build(
        [{"Column 1": "Near Store"}],
        [rst_row("123", "Near Stores")],
        orders={"123": 2419},
    )
    assert result.restaurants[0].mapping_status == MappingStatus.UNMATCHED
    candidate = result.mapping_cases[0].candidates[0]
    assert candidate.canonical_order_count == 2419
    assert "ADMIN ORDERS 2,419" in candidate.similarity_indicators


def test_materially_different_name_blocks_existing_restaurant_id() -> None:
    result = build(
        [{"Column 1": "Completely Different Brand", "Restaurant ID": "123"}],
        [rst_row("123", "O'Tacos Ziraoui")],
    )
    restaurant = result.restaurants[0]
    assert restaurant.mapping_status == MappingStatus.SCOPE_ID_NAME_MISMATCH
    assert restaurant.readiness.identity_ready is False
    assert "SCOPE_ID_NAME_MISMATCH" in restaurant.issue_codes


def test_spelling_variation_does_not_block_exact_restaurant_id() -> None:
    result = build(
        [{"Column 1": "O Tacos - Ziraou", "Restaurant ID": "123"}],
        [rst_row("123", "O'Tacos Ziraoui")],
    )
    assert result.restaurants[0].mapping_status == MappingStatus.MATCHED_BY_ID
    assert result.restaurants[0].readiness.identity_ready is True


def test_conflicting_scope_case_keeps_all_rows_and_fields() -> None:
    result = build(
        [
            {
                "Column 1": "Store A",
                "Restaurant ID": "123",
                "CITY": "Rabat",
                "Commission": "0.20",
                "Comment": "first",
            },
            {
                "Column 1": "Store B",
                "Restaurant ID": "123",
                "CITY": "Casablanca",
                "Commission": "0.22",
                "Comment": "second",
            },
        ],
        [rst_row("123", "Store A")],
    )
    case = result.mapping_cases[0]
    assert case.mapping_status == MappingStatus.CONFLICTING_SCOPE
    assert len(case.scope_rows) == 2
    assert set(case.conflict_fields) >= {
        "restaurant_name",
        "city",
        "commission_rate",
        "comment",
    }


def test_identical_scope_duplicate_keeps_both_source_rows() -> None:
    row = {
        "Column 1": "Store",
        "Restaurant ID": "123",
        "CITY": "Rabat",
        "Commission": "0.20",
    }
    result = build([row, row.copy()], [rst_row("123", "Store", city="Rabat")])
    case = result.mapping_cases[0]
    assert case.mapping_status == MappingStatus.DUPLICATE_SCOPE
    assert case.conflict_fields == ()
    assert tuple(item.source_row for item in case.scope_rows) == (2, 3)


def test_refresh_rebuild_resolves_newly_added_restaurant_id() -> None:
    rst = [rst_row("123", "Target Store")]
    before = build([{"Column 1": "Target Stor"}], rst)
    after = build(
        [{"Column 1": "Target Stor", "Restaurant ID": "123"}],
        rst,
    )
    assert before.restaurants[0].readiness.identity_ready is False
    assert after.restaurants[0].mapping_status == MappingStatus.MATCHED_BY_ID
    assert after.restaurants[0].readiness.identity_ready is True


def test_readiness_dimensions_are_independent() -> None:
    result = build(
        [{"Column 1": "Store", "Restaurant ID": "123"}],
        [rst_row("123", "Store", legal=None, rib=None)],
        orders={"123": 5},
    )
    readiness = result.restaurants[0].readiness
    assert readiness.identity_ready is True
    assert readiness.orders_available is True
    assert readiness.settlement_ready is None
    assert readiness.document_ready is False
    assert readiness.email_ready is True
    assert readiness.payment_ready is False
    assert result.mapped_count == 1
    assert result.mapping_completion == 1.0
    assert result.ready_for_settlement_mapping is True


def test_invalid_scope_id_is_not_found_and_only_suggested() -> None:
    result = build(
        [
            {
                "Column 1": "O'Tacos Ziraoui",
                "Restaurant ID": "does-not-exist",
                "CITY": "Casablanca",
            }
        ],
        [rst_row("123", "O'Tacos Ziraoui", chain="O'Tacos")],
    )
    case = result.mapping_cases[0]
    assert case.mapping_method == "RESTAURANT_ID_NOT_FOUND"
    assert case.identity_ready is False
    assert case.copy_fix.current_restaurant_id == "does-not-exist"
    assert case.copy_fix.suggested_restaurant_id == "123"
    assert result.restaurants[0].restaurant_id == "does-not-exist"


def test_id_name_mismatch_exposes_expected_rst_and_admin_name() -> None:
    result = build(
        [{"Column 1": "Completely Different Brand", "Restaurant ID": "123"}],
        [rst_row("123", "O'Tacos Ziraoui")],
        orders={"123": 7},
        order_names={"123": "O'Tacos Ziraoui Admin"},
    )
    case = result.mapping_cases[0]
    assert case.mapping_status == MappingStatus.SCOPE_ID_NAME_MISMATCH
    assert case.scope_id_rst_candidate is not None
    assert case.scope_id_rst_candidate.restaurant_name == "O'Tacos Ziraoui"
    assert (
        case.scope_id_rst_candidate.admin_restaurant_name
        == "O'Tacos Ziraoui Admin"
    )
    assert case.identity_ready is False


def test_candidate_ranking_is_limited_to_five_and_never_applied() -> None:
    result = build(
        [{"Column 1": "Store", "CITY": "Casablanca"}],
        [rst_row(str(index), f"Store {index}") for index in range(10)],
    )
    case = result.mapping_cases[0]
    assert case.mapping_status == MappingStatus.UNMATCHED
    assert len(case.candidates) == 5
    assert result.mapped_count == 0


def test_conflicting_scope_reason_and_interpretation_are_advisory() -> None:
    result = build(
        [
            {
                "Column 1": "Old Store Name",
                "Restaurant ID": "123",
                "CITY": "Rabat",
                "Commission": "0.20",
            },
            {
                "Column 1": "New Store Name",
                "Restaurant ID": "123",
                "CITY": "Casablanca",
                "Commission": "0.22",
            },
        ],
        [rst_row("123", "New Store Name")],
    )
    case = result.mapping_cases[0]
    assert case.conflict_reason == ScopeConflictReason.MULTI_FIELD_CONFLICT
    assert case.conflict_interpretation in {
        ConflictInterpretation.DIFFERENT_STORES_SHARING_ID,
        ConflictInterpretation.OLD_NEW_RESTAURANT_NAMING,
        ConflictInterpretation.UNCERTAIN,
    }
    assert case.identity_ready is False


def test_single_field_conflict_reasons() -> None:
    city = build(
        [
            {"Column 1": "Store", "Restaurant ID": "123", "CITY": "Rabat"},
            {
                "Column 1": "Store",
                "Restaurant ID": "123",
                "CITY": "Casablanca",
            },
        ],
        [rst_row("123", "Store")],
    )
    commission = build(
        [
            {"Column 1": "Store", "Restaurant ID": "123", "Commission": "0.20"},
            {"Column 1": "Store", "Restaurant ID": "123", "Commission": "0.22"},
        ],
        [rst_row("123", "Store")],
    )
    assert (
        city.mapping_cases[0].conflict_reason
        == ScopeConflictReason.CITY_CONFLICT
    )
    assert (
        commission.mapping_cases[0].conflict_reason
        == ScopeConflictReason.COMMISSION_CONFLICT
    )


def test_no_id_rows_are_classified_and_copy_data_is_available() -> None:
    duplicate = {"Column 1": "Duplicate"}
    result = build(
        [
            {"Column 1": "Exact"},
            {"Column 1": "Ambiguous"},
            {"Column 1": "Unknown"},
            duplicate,
            duplicate.copy(),
        ],
        [
            rst_row("1", "Exact"),
            rst_row("2", "Ambiguous"),
            rst_row("3", "Ambiguous"),
            rst_row("4", "Duplicate"),
        ],
    )
    counts = result.no_id_row_counts()
    assert counts[NoIdClassification.EXACT_NAME_MAPPED] == 1
    assert counts[NoIdClassification.AMBIGUOUS] == 1
    assert counts[NoIdClassification.UNMATCHED] == 1
    assert counts[NoIdClassification.CONFLICTING_OR_DUPLICATE] == 2
    exact = next(
        case
        for case in result.mapping_cases
        if case.mapping_status == MappingStatus.MATCHED_BY_EXACT_NAME
    )
    assert exact.copy_fix.suggested_restaurant_id == "1"


def test_identity_population_is_separated_from_blockers_and_gate() -> None:
    blocked = build(
        [
            {"Column 1": "Exact"},
            {"Column 1": "Unknown"},
        ],
        [rst_row("1", "Exact")],
    )
    ready = build([{"Column 1": "Exact"}], [rst_row("1", "Exact", rib=None)])
    assert len(blocked.identity_ready_restaurants) == 1
    assert len(blocked.identity_blocked_restaurants) == 1
    assert blocked.ready_for_settlement_mapping is False
    assert ready.ready_for_settlement_mapping is True
    assert ready.restaurants[0].readiness.payment_ready is False


def test_confidence_is_advisory_and_does_not_resolve() -> None:
    result = build(
        [{"Column 1": "O'Tacos Ziraou", "CITY": "Casablanca"}],
        [rst_row("123", "O'Tacos Ziraoui", chain="O'Tacos")],
    )
    case = result.mapping_cases[0]
    assert case.correction_confidence in {
        CorrectionConfidence.HIGH_CONFIDENCE,
        CorrectionConfidence.LOW_CONFIDENCE,
    }
    assert result.restaurants[0].readiness.identity_ready is False
