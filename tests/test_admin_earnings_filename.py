from src.ingestion.admin_earnings_filename import parse_admin_earnings_filename


def test_admin_earnings_filename_contract() -> None:
    parsed = parse_admin_earnings_filename("data week 31_2026.xlsx")
    assert parsed is not None
    assert (parsed.week, parsed.year, parsed.extension) == (31, 2026, ".xlsx")


def test_extensionless_filename_is_structurally_valid() -> None:
    parsed = parse_admin_earnings_filename("data week 31_2026")
    assert parsed is not None
    assert parsed.extension is None


def test_filename_rejects_leading_zero_and_invalid_week() -> None:
    assert parse_admin_earnings_filename("data week 02_2026.csv") is None
    assert parse_admin_earnings_filename("data week 54_2026.csv") is None
