from __future__ import annotations

from typing import Any

from src.google.exceptions import DriveConnectionError

LEDGER_TABS = ("SUMMARY", "ORDERS", "ADJUSTMENTS", "PAYMENTS", "AUDIT")


class GoogleSheetsService:
    def __init__(self, api: Any) -> None:
        self._api = api

    def create_restaurant_ledger(self, folder_id: str, title: str) -> str:
        """Create the workbook tabs. Moving to folder is an output-Drive concern."""
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": tab}} for tab in LEDGER_TABS],
        }
        try:
            result = (
                self._api.spreadsheets()
                .create(body=body, fields="spreadsheetId")
                .execute()
            )
            return str(result["spreadsheetId"])
        except Exception as exc:
            raise DriveConnectionError("Restaurant ledger creation failed") from exc

    def read_range(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]:
        try:
            result = (
                self._api.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
            return result.get("values", [])
        except Exception as exc:
            raise DriveConnectionError("Restaurant ledger read failed") from exc

    def append_rows(
        self, spreadsheet_id: str, range_name: str, rows: list[list[Any]]
    ) -> None:
        try:
            (
                self._api.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": rows},
                )
                .execute()
            )
        except Exception as exc:
            raise DriveConnectionError("Restaurant ledger append failed") from exc
