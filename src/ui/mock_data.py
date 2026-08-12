import pandas as pd

KPI_ITEMS = [
    ("Eligible partners", "423", "Payment scope population"),
    ("Total orders", "18,642", "+4.8% vs previous period"),
    ("Gross sales", "4.85M MAD", "Eligible orders only"),
    ("Net payable", "3.90M MAD", "After commission & adjustments"),
    ("Ready", "390", "92.2% of partners"),
    ("Needs review", "16", "7 settlement reviews"),
    ("Blocked", "17", "Resolve before validation"),
    ("Documents", "0", "Admin activation required"),
    ("Emails sent", "0", "Waiting for authorization"),
    ("Paid", "0", "Period remains open"),
]


def billing_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                "Chrono Pizza Maarif",
                "RST-001",
                "Chrono Pizza",
                184250,
                36850,
                0,
                147400,
                "Ready",
                "Waiting authorization",
            ],
            [
                "Sushi House Agdal",
                "RST-042",
                "Sushi House",
                152880,
                30576,
                1250,
                123554,
                "Review",
                "Blocked",
            ],
            [
                "Le Patio",
                "RST-118",
                "Standalone",
                96340,
                19268,
                0,
                77072,
                "Validated",
                "Waiting authorization",
            ],
            [
                "Burger Lab Rabat",
                "RST-204",
                "Burger Lab",
                88750,
                17750,
                -450,
                70550,
                "Blocked",
                "Missing email",
            ],
            [
                "Dar Tajine",
                "RST-319",
                "Standalone",
                74400,
                14880,
                0,
                59520,
                "Ready",
                "Waiting authorization",
            ],
        ],
        columns=[
            "Restaurant",
            "Restaurant ID",
            "Chain",
            "Gross",
            "Commission",
            "Adjustment",
            "Net payable",
            "Settlement",
            "Email",
        ],
    )


WORKFLOW = pd.DataFrame(
    {
        "Status": ["Ready", "Needs review", "Blocked", "Validated"],
        "Partners": [390, 16, 17, 0],
    }
)

EMAIL_FUNNEL = pd.DataFrame(
    {
        "Stage": [
            "Eligible",
            "Email ready",
            "Missing email",
            "Blocked",
            "Admin authorized",
            "Sent",
        ],
        "Partners": [423, 396, 8, 19, 0, 0],
    }
)
