"""The Spark cell: object-store datasets and the column mappings between them."""

from pathlib import Path

from lineage_api.services.spark_sca import analyze_spark_sources

ROOT = Path(__file__).resolve().parents[4]
REPO = ROOT / "fixtures" / "repositories" / "spark-orders-etl"


def _analyze():
    return analyze_spark_sources(
        {
            item.relative_to(REPO).as_posix(): item.read_text()
            for item in sorted(REPO.rglob("*.java"))
        }
    )


def test_a_literal_read_path_becomes_a_source_dataset() -> None:
    analysis = _analyze()

    assert "s3://raw-lake/raw/events/orders" in {j.source for j in analysis.jobs}


def test_a_literal_write_path_becomes_a_target_dataset() -> None:
    analysis = _analyze()

    job = next(j for j in analysis.jobs if j.target == "s3://raw-lake/curated/orders")
    assert job.source == "s3://raw-lake/raw/events/orders"
    assert job.method == "OrdersCurationJob#run"


def test_with_column_yields_column_level_mappings_with_their_transform() -> None:
    analysis = _analyze()

    job = next(j for j in analysis.jobs if j.target.endswith("curated/orders"))
    mappings = {m.target_column: (m.source_columns, m.transform) for m in job.mappings}

    assert mappings["order_id"] == (("order_id",), "col(order_id)")
    assert mappings["order_total"] == (("amount",), "sum(col(amount))")
    assert mappings["order_date"] == (("occurred_at",), "to_date(col(occurred_at))")


def test_a_path_built_from_a_variable_is_residue_not_a_guess() -> None:
    analysis = _analyze()

    assert "dynamic-path" in {r.code for r in analysis.residue}
    assert all("curated/" != j.target for j in analysis.jobs)


def test_analysis_is_deterministic() -> None:
    assert _analyze() == _analyze()


def test_a_kafka_streaming_source_is_read_from_its_subscribe_option() -> None:
    """`readStream().format("kafka").option("subscribe", t)` names the topic."""
    analysis = _analyze()

    job = next(j for j in analysis.jobs if j.method.startswith("OrdersLandingJob"))
    assert job.source == "inventory-count-events"
    assert job.target == "s3://raw-lake/raw/events/orders"


def test_a_streaming_sink_path_option_is_the_target() -> None:
    analysis = _analyze()

    assert "s3://raw-lake/raw/events/orders" in {j.target for j in analysis.jobs}
