from __future__ import annotations

from src.google.interfaces import SheetsService
from src.models.domain import Restaurant
from src.restaurants.registry import RestaurantRegistryService


class RestaurantLedgerProvisioner:
    def __init__(
        self, sheets: SheetsService, registry: RestaurantRegistryService
    ) -> None:
        self.sheets = sheets
        self.registry = registry

    def ensure_ledger(self, restaurant: Restaurant, folder_id: str) -> str:
        existing = self.registry.get_spreadsheet_id(restaurant.restaurant_id)
        if existing:
            return existing
        spreadsheet_id = self.sheets.create_restaurant_ledger(
            folder_id,
            f"{restaurant.restaurant_id} · {restaurant.restaurant_name} · CashCo",
        )
        self.registry.set_spreadsheet_id(restaurant.restaurant_id, spreadsheet_id)
        return spreadsheet_id
