"""Static extraction of the service interactions plane from Java sources.

Inbound: what a service exposes — Spring MVC mapping annotations on a `@RestController`
or `@Controller`, with the class-level `@RequestMapping` prefix composed in.

Outbound: what a service calls — declarative `@FeignClient` interfaces, whose `name`
attribute states the target service directly.

Every route and every target must be a **string literal**. A mapping built from a
constant or an expression is residue, never a guessed route: the same discipline the
dataset cells apply to table names.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from lineage_api.domain.interactions import InteractionField

_PARSER = Parser(Language(tree_sitter_java.language()))

_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_CONTROLLER_ANNOTATIONS = {"RestController", "Controller"}
_GRAPHQL_ANNOTATIONS = {
    "QueryMapping": "query",
    "MutationMapping": "mutation",
    "SubscriptionMapping": "subscription",
}
_ASYNC_ANNOTATIONS = {"KafkaListener": "topics", "RabbitListener": "queues"}
_GRPC_ANNOTATION = "GrpcService"
# RestTemplate exposes the verb in the method name; WebClient exposes it as a builder
# step before `.uri(...)`.
_REST_TEMPLATE_VERBS = {
    "getForObject": "GET",
    "getForEntity": "GET",
    "postForObject": "POST",
    "postForEntity": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "exchange": "EXCHANGE",
}
_WEB_CLIENT_VERBS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
}

# The prototype models exactly these four channels; each must have an extractor.
SUPPORTED_CHANNELS = frozenset({"REST", "GRPC", "GRAPHQL", "ASYNC_EVENT"})


@dataclass(frozen=True, slots=True)
class InboundEndpoint:
    service: str
    channel: str
    operation: str
    handler: str
    request_fields: tuple[InteractionField, ...]
    response_fields: tuple[InteractionField, ...]
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class OutboundCall:
    from_service: str
    to_service: str
    channel: str
    operation: str
    handler: str
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class InteractionResidue:
    code: str
    path: str
    line: int
    symbol: str


@dataclass(frozen=True, slots=True)
class InteractionAnalysis:
    inbound: tuple[InboundEndpoint, ...]
    outbound: tuple[OutboundCall, ...]
    residue: tuple[InteractionResidue, ...]


def _text(node: Node, content: bytes) -> str:
    return content[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _annotations(declaration: Node) -> list[Node]:
    modifiers = next(
        (child for child in declaration.children if child.type == "modifiers"), None
    )
    if modifiers is None:
        return []
    return [
        child
        for child in modifiers.children
        if child.type in {"annotation", "marker_annotation"}
    ]


def _annotation_name(annotation: Node, content: bytes) -> str:
    for child in annotation.children:
        if child.type in {"identifier", "scoped_identifier"}:
            return _text(child, content).rsplit(".", 1)[-1]
    return ""


def _string_argument(annotation: Node, content: bytes) -> str | bool:
    """The single literal argument, `False` when present but not a literal."""
    arguments = next(
        (c for c in annotation.children if c.type == "annotation_argument_list"), None
    )
    if arguments is None:
        return ""
    literals = [c for c in arguments.children if c.type == "string_literal"]
    if literals:
        return _text(literals[0], content).strip('"')
    pairs = [c for c in arguments.children if c.type == "element_value_pair"]
    for pair in pairs:
        value = pair.children[-1]
        if value.type == "string_literal":
            return _text(value, content).strip('"')
        return False
    if any(c.type not in {"(", ")"} for c in arguments.children):
        return False
    return ""


def _named_argument(annotation: Node, content: bytes, name: str) -> str | bool:
    arguments = next(
        (c for c in annotation.children if c.type == "annotation_argument_list"), None
    )
    if arguments is None:
        return False
    for pair in (c for c in arguments.children if c.type == "element_value_pair"):
        key = _text(pair.children[0], content)
        value = pair.children[-1]
        if key != name:
            continue
        if value.type == "string_literal":
            return _text(value, content).strip('"')
        return False
    literals = [c for c in arguments.children if c.type == "string_literal"]
    if literals:
        return _text(literals[0], content).strip('"')
    return False


def _join_route(prefix: str, suffix: str) -> str:
    combined = f"{prefix.rstrip('/')}/{suffix.lstrip('/')}" if suffix else prefix
    return combined if combined.startswith("/") else f"/{combined}"


def _parameters(declaration: Node, content: bytes) -> tuple[InteractionField, ...]:
    parameters = next(
        (c for c in declaration.children if c.type == "formal_parameters"), None
    )
    if parameters is None:
        return ()
    fields: list[InteractionField] = []
    for parameter in (
        c for c in parameters.children if c.type == "formal_parameter"
    ):
        named = [c for c in parameter.children if c.type not in {",", "(", ")"}]
        if len(named) < 2:
            continue
        fields.append(
            InteractionField(
                name=_text(named[-1], content), type=_text(named[-2], content)
            )
        )
    return tuple(fields)


def _return_field(declaration: Node, content: bytes) -> tuple[InteractionField, ...]:
    for child in declaration.children:
        if child.type in {"type_identifier", "generic_type", "void_type"}:
            declared = _text(child, content)
            if declared == "void":
                return ()
            return (InteractionField(name="body", type=declared),)
    return ()


def _method_declarations(body: Node) -> list[Node]:
    return [child for child in body.children if child.type == "method_declaration"]


def _method_name(declaration: Node, content: bytes) -> str:
    for child in declaration.children:
        if child.type == "identifier":
            return _text(child, content)
    return ""


def analyze_java_interactions(
    sources: dict[str, str], *, service: str
) -> InteractionAnalysis:
    inbound: list[InboundEndpoint] = []
    outbound: list[OutboundCall] = []
    residue: list[InteractionResidue] = []

    for path in sorted(sources):
        content = sources[path].encode()
        root = _PARSER.parse(content).root_node
        for declaration in root.children:
            if declaration.type not in {"class_declaration", "interface_declaration"}:
                continue
            annotations = {
                _annotation_name(item, content): item
                for item in _annotations(declaration)
            }
            body = next(
                (
                    c
                    for c in declaration.children
                    if c.type in {"class_body", "interface_body"}
                ),
                None,
            )
            if body is None:
                continue
            type_name = next(
                (
                    _text(c, content)
                    for c in declaration.children
                    if c.type == "identifier"
                ),
                "",
            )

            feign = annotations.get("FeignClient")
            if feign is not None:
                target = _named_argument(feign, content, "name")
                if target is False or not target:
                    residue.append(
                        InteractionResidue(
                            "unresolved-service",
                            path,
                            feign.start_point[0] + 1,
                            type_name,
                        )
                    )
                    continue
                for method in _method_declarations(body):
                    verb, route = _route_of(method, content)
                    if verb is None:
                        continue
                    if route is False:
                        residue.append(
                            InteractionResidue(
                                "dynamic-route",
                                path,
                                method.start_point[0] + 1,
                                _method_name(method, content),
                            )
                        )
                        continue
                    outbound.append(
                        OutboundCall(
                            from_service=service,
                            to_service=str(target),
                            channel="REST",
                            operation=f"{verb} {_join_route('', str(route))}",
                            handler=f"{type_name}#{_method_name(method, content)}",
                            path=path,
                            line=method.start_point[0] + 1,
                        )
                    )
                continue

            if _GRPC_ANNOTATION in annotations:
                for method in _method_declarations(body):
                    inbound.append(
                        InboundEndpoint(
                            service=service,
                            channel="GRPC",
                            operation=(
                                f"{type_name}/{_method_name(method, content)}"
                            ),
                            handler=f"{type_name}#{_method_name(method, content)}",
                            request_fields=_parameters(method, content),
                            response_fields=_return_field(method, content),
                            path=path,
                            line=method.start_point[0] + 1,
                        )
                    )
                continue

            graphql_or_async = _non_rest_methods(body, content)
            if graphql_or_async:
                for method, channel, operation in graphql_or_async:
                    if operation is False:
                        residue.append(
                            InteractionResidue(
                                "dynamic-topic",
                                path,
                                method.start_point[0] + 1,
                                _method_name(method, content),
                            )
                        )
                        continue
                    inbound.append(
                        InboundEndpoint(
                            service=service,
                            channel=channel,
                            operation=str(operation),
                            handler=f"{type_name}#{_method_name(method, content)}",
                            request_fields=_parameters(method, content),
                            response_fields=_return_field(method, content),
                            path=path,
                            line=method.start_point[0] + 1,
                        )
                    )
                continue

            if not (_CONTROLLER_ANNOTATIONS & annotations.keys()):
                continue

            prefix_annotation = annotations.get("RequestMapping")
            prefix = ""
            if prefix_annotation is not None:
                raw = _string_argument(prefix_annotation, content)
                if raw is False:
                    residue.append(
                        InteractionResidue(
                            "dynamic-route",
                            path,
                            prefix_annotation.start_point[0] + 1,
                            type_name,
                        )
                    )
                    continue
                prefix = str(raw)

            for method in _method_declarations(body):
                verb, route = _route_of(method, content)
                if verb is None:
                    continue
                if route is False:
                    residue.append(
                        InteractionResidue(
                            "dynamic-route",
                            path,
                            method.start_point[0] + 1,
                            _method_name(method, content),
                        )
                    )
                    continue
                inbound.append(
                    InboundEndpoint(
                        service=service,
                        channel="REST",
                        operation=f"{verb} {_join_route(prefix, str(route))}",
                        handler=f"{type_name}#{_method_name(method, content)}",
                        request_fields=_parameters(method, content),
                        response_fields=_return_field(method, content),
                        path=path,
                        line=method.start_point[0] + 1,
                    )
                )

        for call, residue_entry in _http_client_calls(root, content, path, service):
            if residue_entry is not None:
                residue.append(residue_entry)
            else:
                outbound.append(call)

    return InteractionAnalysis(
        inbound=tuple(sorted(inbound, key=lambda item: (item.path, item.line))),
        outbound=tuple(sorted(outbound, key=lambda item: (item.path, item.line))),
        residue=tuple(sorted(residue, key=lambda item: (item.path, item.line))),
    )


def _route_of(method: Node, content: bytes) -> tuple[str | None, str | bool]:
    for annotation in _annotations(method):
        verb = _METHOD_ANNOTATIONS.get(_annotation_name(annotation, content))
        if verb is None:
            continue
        return verb, _string_argument(annotation, content)
    return None, ""


def _non_rest_methods(
    body: Node, content: bytes
) -> list[tuple[Node, str, str | bool]]:
    """GraphQL mappings and async listeners declared on a class's methods.

    Returned as (method, channel, operation), where `operation` is False when the
    declaration is present but its name is not a literal — residue, never a guess.
    """
    found: list[tuple[Node, str, str | bool]] = []
    for method in _method_declarations(body):
        for annotation in _annotations(method):
            name = _annotation_name(annotation, content)
            operation_kind = _GRAPHQL_ANNOTATIONS.get(name)
            if operation_kind is not None:
                declared = _string_argument(annotation, content)
                label = (
                    _method_name(method, content)
                    if declared in ("", False)
                    else str(declared)
                )
                found.append((method, "GRAPHQL", f"{operation_kind} {label}"))
                break
            attribute = _ASYNC_ANNOTATIONS.get(name)
            if attribute is not None:
                topic = _named_argument(annotation, content, attribute)
                found.append((method, "ASYNC_EVENT", topic))
                break
    return found


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _invocation_name(node: Node, content: bytes) -> str:
    field = node.child_by_field_name("name")
    return _text(field, content) if field is not None else ""


def _split_service_url(url: str) -> tuple[str, str] | None:
    """`http://customers-service/owners/{id}` -> ("customers-service", "/owners/{id}")."""
    for scheme in ("http://", "https://"):
        if url.startswith(scheme):
            remainder = url[len(scheme) :]
            host, separator, path = remainder.partition("/")
            if not host:
                return None
            route = f"/{path}" if separator else "/"
            return host, route.split("?", 1)[0]
    return None


def _http_client_calls(root: Node, content: bytes, path: str, service: str):
    """Outbound REST calls whose target and route are provable from a literal URL.

    A URL assembled from a variable is `dynamic-endpoint` residue: the target service
    cannot be proven without executing the code, and guessing it would invent an edge.
    """
    for node in _walk(root):
        if node.type != "method_invocation":
            continue
        name = _invocation_name(node, content)
        verb = _REST_TEMPLATE_VERBS.get(name)
        is_web_client_uri = name == "uri"
        if verb is None and not is_web_client_uri:
            continue

        arguments = node.child_by_field_name("arguments")
        first = next(
            (c for c in arguments.children if c.type not in {"(", ")", ","}),
            None,
        ) if arguments is not None else None
        if first is None:
            continue
        if first.type != "string_literal":
            yield None, InteractionResidue(
                "dynamic-endpoint", path, node.start_point[0] + 1, name
            )
            continue

        target = _split_service_url(_text(first, content).strip('"'))
        if target is None:
            yield None, InteractionResidue(
                "unresolved-service", path, node.start_point[0] + 1, name
            )
            continue

        if is_web_client_uri:
            receiver = node.child_by_field_name("object")
            verb = None
            while receiver is not None and receiver.type == "method_invocation":
                verb = _WEB_CLIENT_VERBS.get(_invocation_name(receiver, content))
                if verb is not None:
                    break
                receiver = receiver.child_by_field_name("object")
            if verb is None:
                yield None, InteractionResidue(
                    "unresolved-service", path, node.start_point[0] + 1, name
                )
                continue

        host, route = target
        yield (
            OutboundCall(
                from_service=service,
                to_service=host,
                channel="REST",
                operation=f"{verb} {route}",
                handler=name,
                path=path,
                line=node.start_point[0] + 1,
            ),
            None,
        )
