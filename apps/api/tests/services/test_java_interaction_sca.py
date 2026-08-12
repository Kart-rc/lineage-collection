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


# --- channels beyond REST -------------------------------------------------------------


def test_spring_graphql_mappings_are_graphql_interactions() -> None:
    analysis = analyze_java_interactions(
        {
            "G.java": (
                "package example;\n"
                "import org.springframework.graphql.data.method.annotation.QueryMapping;\n"
                "import org.springframework.graphql.data.method.annotation.MutationMapping;\n"
                "import org.springframework.stereotype.Controller;\n"
                "@Controller\n"
                "public class CustomerGraph {\n"
                "  @QueryMapping\n"
                "  public CustomerView customer(Integer id) { return null; }\n"
                "  @MutationMapping\n"
                "  public CustomerView updateCustomer(CustomerInput input) { return null; }\n"
                "}\n"
            )
        },
        service="customer-service",
    )

    assert [(i.channel, i.operation) for i in analysis.inbound] == [
        ("GRAPHQL", "query customer"),
        ("GRAPHQL", "mutation updateCustomer"),
    ]


def test_a_kafka_listener_is_an_async_event_interaction() -> None:
    analysis = analyze_java_interactions(
        {
            "K.java": (
                "package example;\n"
                "import org.springframework.kafka.annotation.KafkaListener;\n"
                "import org.springframework.stereotype.Component;\n"
                "@Component\n"
                "public class PaymentEvents {\n"
                '  @KafkaListener(topics = "payment.completed")\n'
                "  public void onPaymentCompleted(PaymentEvent event) { }\n"
                "}\n"
            )
        },
        service="orders-service",
    )

    endpoint = analysis.inbound[0]
    assert endpoint.channel == "ASYNC_EVENT"
    assert endpoint.operation == "payment.completed"
    assert [(f.name, f.type) for f in endpoint.request_fields] == [
        ("event", "PaymentEvent")
    ]


def test_a_grpc_service_method_is_a_grpc_interaction() -> None:
    analysis = analyze_java_interactions(
        {
            "I.java": (
                "package example;\n"
                "import net.devh.boot.grpc.server.service.GrpcService;\n"
                "@GrpcService\n"
                "public class InventoryService {\n"
                "  public ReserveReply reserve(ReserveRequest request) { return null; }\n"
                "}\n"
            )
        },
        service="inventory-service",
    )

    endpoint = analysis.inbound[0]
    assert endpoint.channel == "GRPC"
    assert endpoint.operation == "InventoryService/reserve"


def test_a_kafka_topic_that_is_not_a_literal_is_residue() -> None:
    analysis = analyze_java_interactions(
        {
            "K.java": (
                "package example;\n"
                "import org.springframework.kafka.annotation.KafkaListener;\n"
                "import org.springframework.stereotype.Component;\n"
                "@Component\n"
                "public class E {\n"
                "  @KafkaListener(topics = TOPIC)\n"
                "  public void on(Event event) { }\n"
                "}\n"
            )
        },
        service="s",
    )

    assert analysis.inbound == ()
    assert [r.code for r in analysis.residue] == ["dynamic-topic"]


def test_every_prototype_channel_has_an_extractor() -> None:
    """The prototype models four channels; all four must be reachable."""
    from lineage_api.services.java_interaction_sca import SUPPORTED_CHANNELS

    assert SUPPORTED_CHANNELS == frozenset(
        {"REST", "GRPC", "GRAPHQL", "ASYNC_EVENT"}
    )


# --- outbound WebClient / RestTemplate call sites --------------------------------------


def test_a_webclient_call_with_a_literal_uri_names_its_target_service() -> None:
    analysis = analyze_java_interactions(
        {
            "C.java": (
                "package example;\n"
                "import org.springframework.web.reactive.function.client.WebClient;\n"
                "public class CustomersServiceClient {\n"
                "  private final WebClient.Builder webClientBuilder;\n"
                "  public Object getOwner(int ownerId) {\n"
                "    return webClientBuilder.build().get()\n"
                '      .uri("http://customers-service/owners/{ownerId}", ownerId)\n'
                "      .retrieve().bodyToMono(Object.class);\n"
                "  }\n"
                "}\n"
            )
        },
        service="api-gateway",
    )

    call = analysis.outbound[0]
    assert call.from_service == "api-gateway"
    assert call.to_service == "customers-service"
    assert call.channel == "REST"
    assert call.operation == "GET /owners/{ownerId}"


def test_a_resttemplate_call_with_a_literal_uri_resolves() -> None:
    analysis = analyze_java_interactions(
        {
            "R.java": (
                "package example;\n"
                "import org.springframework.web.client.RestTemplate;\n"
                "public class VetClient {\n"
                "  private final RestTemplate restTemplate = new RestTemplate();\n"
                "  public Object vets() {\n"
                '    return restTemplate.getForObject("http://vets-service/vets", Object.class);\n'
                "  }\n"
                "}\n"
            )
        },
        service="api-gateway",
    )

    call = analysis.outbound[0]
    assert call.to_service == "vets-service"
    assert call.operation == "GET /vets"


def test_a_uri_built_from_a_variable_is_residue() -> None:
    analysis = analyze_java_interactions(
        {
            "V.java": (
                "package example;\n"
                "import org.springframework.web.reactive.function.client.WebClient;\n"
                "public class VisitsServiceClient {\n"
                "  private final String hostname = \"http://visits-service/\";\n"
                "  public Object visits(Object petIds) {\n"
                "    return webClientBuilder.build().get()\n"
                '      .uri(hostname + "pets/visits?petId={petId}", petIds)\n'
                "      .retrieve();\n"
                "  }\n"
                "}\n"
            )
        },
        service="api-gateway",
    )

    assert analysis.outbound == ()
    assert "dynamic-endpoint" in {item.code for item in analysis.residue}
