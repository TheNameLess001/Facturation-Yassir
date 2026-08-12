from __future__ import annotations

import pandas as pd

from src.restaurants.mapping_review import CandidateRankingService
from src.restaurants.registry_models import (
    MappingStatus,
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


def build(scope_rows, rst_rows, *, orders=None):
    scope = pd.DataFrame(scope_rows)
    rst = pd.DataFrame(rst_rows)
    return RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
        canonical_order_counts=orders or {},
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
