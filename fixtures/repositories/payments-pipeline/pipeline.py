"""Seeded analysis fixture; functions are declarations for the static analyzer."""


def build_daily_revenue() -> None:
    transactions = read_dataset(
        "snowflake://payments/raw.transactions",
        elements=["customer_id", "amount", "occurred_at"],
    )
    write_dataset(
        "analytics.daily_revenue",
        sources=[transactions],
        mappings={
            "customer_id": "customer_id",
            "gross_revenue": "SUM(amount)",
            "revenue_date": "DATE(occurred_at)",
        },
    )


def unresolved_dynamic_source(tenant: str) -> None:
    dynamic_dataset_name = f"tenant_{tenant}.transactions"
    read_dataset(dynamic_dataset_name)
