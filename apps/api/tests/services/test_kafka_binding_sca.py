"""Spring Cloud Stream declares its topics in configuration, not in code.

Every shape below was taken from real files in the spring-cloud-stream-samples
repository, so the reader is held to what actually ships rather than to the
documentation's happy path.
"""

import pytest

from lineage_api.services.kafka_binding_sca import (
    StreamBinding,
    read_stream_bindings,
)


def _bindings(path: str, text: str):
    return read_stream_bindings({path: text})


# --- real YAML shapes ------------------------------------------------------------------


def test_flat_dotted_keys_with_a_nested_destination() -> None:
    analysis = _bindings(
        "src/main/resources/application.yml",
        "spring.application.name: inventory-processor\n"
        "\n"
        "spring.cloud.stream.bindings.process-in-0:\n"
        "  destination: inventory-update-events\n"
        "spring.cloud.stream.bindings.process-out-0:\n"
        "  destination: inventory-count-events\n",
    )

    assert [(b.binding, b.direction, b.topic) for b in analysis.bindings] == [
        ("process-in-0", "READS", "inventory-update-events"),
        ("process-out-0", "WRITES", "inventory-count-events"),
    ]


def test_a_fully_nested_document() -> None:
    analysis = _bindings(
        "application.yml",
        "spring:\n"
        "  cloud:\n"
        "    stream:\n"
        "      bindings:\n"
        "        process-in-0:\n"
        "          destination: user-clicks\n"
        "        process-out-0:\n"
        "          destination: enriched-clicks\n",
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "user-clicks"),
        ("WRITES", "enriched-clicks"),
    ]


def test_the_shorthand_where_the_value_is_the_destination() -> None:
    analysis = _bindings(
        "application.yml",
        "spring:\n"
        "  cloud:\n"
        "    stream:\n"
        "      bindings:\n"
        "        process-in-0: words\n"
        "        process-out-0: counts\n",
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "words"),
        ("WRITES", "counts"),
    ]


def test_a_multi_input_join_reads_every_input_topic() -> None:
    """kafka-streams-table-join: two inputs, one output."""
    analysis = _bindings(
        "application.yml",
        "spring.cloud.stream.bindings.process-in-0:\n"
        "  destination: user-clicks\n"
        "spring.cloud.stream.bindings.process-in-1:\n"
        "  destination: user-regions\n"
        "spring.cloud.stream.bindings.process-out-0:\n"
        "  destination: output-topic\n",
    )

    reads = [b.topic for b in analysis.bindings if b.direction == "READS"]
    writes = [b.topic for b in analysis.bindings if b.direction == "WRITES"]
    assert reads == ["user-clicks", "user-regions"]
    assert writes == ["output-topic"]


# --- properties ------------------------------------------------------------------------


def test_properties_format() -> None:
    analysis = _bindings(
        "src/main/resources/application.properties",
        "# a comment\n"
        "spring.cloud.stream.bindings.handle-in-0.destination=testEmbeddedIn\n"
        "spring.cloud.stream.bindings.handle-out-0.destination=testEmbeddedOut\n"
        "spring.cloud.stream.bindings.handle-in-0.group=embeddedKafkaApplication\n",
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "testEmbeddedIn"),
        ("WRITES", "testEmbeddedOut"),
    ]


def test_the_legacy_input_and_output_binding_names() -> None:
    """Pre-functional style, still present in real samples."""
    analysis = _bindings(
        "application.properties",
        "spring.cloud.stream.bindings.input.destination=metrics-demo-in\n"
        "spring.cloud.stream.bindings.output.destination=metrics-demo-out\n",
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "metrics-demo-in"),
        ("WRITES", "metrics-demo-out"),
    ]


# --- discipline ------------------------------------------------------------------------


def test_a_binding_whose_direction_is_not_provable_is_residue() -> None:
    analysis = _bindings(
        "application.yml",
        "spring.cloud.stream.bindings.sideways:\n  destination: mystery\n",
    )

    assert analysis.bindings == ()
    assert [r.code for r in analysis.residue] == ["unrecognised-binding-name"]


def test_a_destination_built_from_a_placeholder_is_residue() -> None:
    """`${...}` is resolved at deploy time; the topic is not knowable from source."""
    analysis = _bindings(
        "application.yml",
        "spring.cloud.stream.bindings.process-in-0:\n  destination: ${TOPIC_NAME}\n",
    )

    assert analysis.bindings == ()
    assert [r.code for r in analysis.residue] == ["dynamic-destination"]


def test_unrelated_configuration_is_ignored_entirely() -> None:
    analysis = _bindings(
        "application.yml",
        "server:\n  port: 8080\nlogging:\n  level:\n    root: INFO\n",
    )

    assert analysis.bindings == ()
    assert analysis.residue == ()


def test_bindings_are_deterministically_ordered_across_files() -> None:
    sources = {
        "b/application.yml": "spring.cloud.stream.bindings.process-out-0:\n  destination: t2\n",
        "a/application.yml": "spring.cloud.stream.bindings.process-in-0:\n  destination: t1\n",
    }

    first = read_stream_bindings(sources)
    second = read_stream_bindings(dict(reversed(list(sources.items()))))

    assert first == second
    assert [b.path for b in first.bindings] == ["a/application.yml", "b/application.yml"]


def test_a_binding_records_its_function_and_citation() -> None:
    analysis = _bindings(
        "application.yml",
        "spring.cloud.stream.bindings.process-in-0:\n  destination: orders\n",
    )

    binding = analysis.bindings[0]
    # The citation points at the line carrying the topic literal, not at the binding
    # declaration above it — that is the line which actually proves the name.
    assert binding == StreamBinding(
        binding="process-in-0",
        function="process",
        direction="READS",
        topic="orders",
        path="application.yml",
        line=2,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("process-in-0", "READS"),
        ("process-in-12", "READS"),
        ("handle-out-0", "WRITES"),
        ("input", "READS"),
        ("output", "WRITES"),
    ],
)
def test_direction_is_derived_from_the_documented_convention(name, expected) -> None:
    analysis = _bindings(
        "application.yml",
        f"spring.cloud.stream.bindings.{name}:\n  destination: t\n",
    )

    assert analysis.bindings[0].direction == expected


# --- raw Kafka Streams topology API ----------------------------------------------------


def test_the_raw_streams_topology_api_names_its_topics() -> None:
    """confluentinc/kafka-streams-examples uses the builder API, not Spring config."""
    from lineage_api.services.kafka_binding_sca import read_streams_topology

    analysis = read_streams_topology(
        {
            "WordCount.java": (
                "package io.confluent.examples.streams;\n"
                "import org.apache.kafka.streams.StreamsBuilder;\n"
                "public class WordCountLambdaExample {\n"
                "  static void createStream(StreamsBuilder builder) {\n"
                '    final KStream<String, String> input = builder.stream("my-input-topic");\n'
                "    input.flatMapValues(v -> Arrays.asList(v.split(\" \")))\n"
                '         .to("my-output-topic", Produced.with(Serdes.String(), Serdes.Long()));\n'
                "  }\n"
                "}\n"
            )
        }
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "my-input-topic"),
        ("WRITES", "my-output-topic"),
    ]


def test_a_ktable_source_is_also_a_read() -> None:
    from lineage_api.services.kafka_binding_sca import read_streams_topology

    analysis = read_streams_topology(
        {"T.java": 'import org.apache.kafka.streams.StreamsBuilder;\nclass T { void f(StreamsBuilder b) { b.table("UserRegions"); } }'}
    )

    assert [(b.direction, b.topic) for b in analysis.bindings] == [
        ("READS", "UserRegions")
    ]


def test_a_topology_topic_built_from_a_variable_is_residue() -> None:
    from lineage_api.services.kafka_binding_sca import read_streams_topology

    analysis = read_streams_topology(
        {"T.java": "import org.apache.kafka.streams.StreamsBuilder;\nclass T { void f(StreamsBuilder b, String t) { b.stream(t); } }"}
    )

    assert analysis.bindings == ()
    assert [r.code for r in analysis.residue] == ["dynamic-destination"]


def test_an_unrelated_to_call_is_not_a_topic() -> None:
    """`.to(...)` on something that is not a stream must not invent a topic."""
    from lineage_api.services.kafka_binding_sca import read_streams_topology

    analysis = read_streams_topology(
        {"T.java": 'class T { void f() { collect.to("not-a-topic"); } }'}
    )

    assert analysis.bindings == ()
