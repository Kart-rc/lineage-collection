from pathlib import Path

from lineage_api.services.java_interaction_sca import analyze_java_interactions

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "fixtures" / "repositories" / "java-services-corpus"


def _corpus() -> dict[str, str]:
    return {
        item.relative_to(CORPUS).as_posix(): item.read_text()
        for item in sorted(CORPUS.rglob("*.java"))
    }


def _analyze():
    return analyze_java_interactions(_corpus(), service="orders-service")


def test_inbound_endpoints_compose_the_class_level_prefix() -> None:
    analysis = _analyze()

    operations = [item.operation for item in analysis.inbound]

    assert operations == ["GET /orders/{id}", "POST /orders"]


def test_an_endpoint_records_its_handler_and_citation() -> None:
    analysis = _analyze()

    endpoint = analysis.inbound[0]

    assert endpoint.handler == "OrderController#findOrder"
    assert endpoint.service == "orders-service"
    assert endpoint.channel == "REST"
    assert endpoint.path.endswith("OrderController.java")
    assert endpoint.line > 0


def test_a_method_without_a_mapping_annotation_is_not_an_endpoint() -> None:
    analysis = _analyze()

    assert "notAnEndpoint" not in {item.handler for item in analysis.inbound}


def test_request_and_response_fields_are_typed_metadata_only() -> None:
    analysis = _analyze()

    endpoint = analysis.inbound[0]

    assert [(f.name, f.type) for f in endpoint.request_fields] == [("id", "Integer")]
    assert [(f.name, f.type) for f in endpoint.response_fields] == [
        ("body", "OrderView")
    ]
    assert all(f.classification == "NONE" for f in endpoint.response_fields)


def test_a_feign_client_names_its_target_service_exactly() -> None:
    analysis = _analyze()

    call = next(item for item in analysis.outbound if item.to_service == "customer-service")

    assert call.from_service == "orders-service"
    assert call.operation == "GET /customers/{id}"
    assert call.handler == "CustomerClient#findCustomer"


def test_a_feign_target_that_is_not_a_literal_is_residue() -> None:
    analysis = _analyze()

    assert "DynamicClient" in {
        item.symbol for item in analysis.residue if item.code == "unresolved-service"
    }
    assert "DynamicClient" not in {item.handler.split("#")[0] for item in analysis.outbound}


def test_classification_is_never_inferred_from_a_field_name() -> None:
    """An `email` parameter is not PII unless something declares it so."""
    analysis = analyze_java_interactions(
        {
            "A.java": (
                "package example;\n"
                "import org.springframework.web.bind.annotation.GetMapping;\n"
                "import org.springframework.web.bind.annotation.RestController;\n"
                "@RestController\n"
                "public class A {\n"
                '  @GetMapping("/x")\n'
                "  public String find(String email) { return null; }\n"
                "}\n"
            )
        },
        service="s",
    )

    endpoint = analysis.inbound[0]
    assert endpoint.request_fields[0].name == "email"
    assert endpoint.request_fields[0].classification == "NONE"


def test_analysis_is_deterministic() -> None:
    assert _analyze() == _analyze()
