"""Join per-repository analyzer documents into one graph.

Repository boundaries are an artefact of how code is stored, not of how data flows.
Two repositories compose when they name the same dataset URN; this module makes that
seam explicit, so an empty seam set is a visible finding rather than a silent island.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepositoryContribution:
    repo: str
    edge_count: int
    datasets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComposedGraph:
    edges: tuple[dict, ...]
    contributions: tuple[RepositoryContribution, ...]
    shared_datasets: tuple[str, ...]


def _dataset_of(urn: str) -> str:
    return urn.rsplit("#", 1)[0]


def _edge_key(edge: dict) -> tuple[str, str, str]:
    sources = ",".join(sorted(str(item) for item in edge.get("from", ())))
    return (sources, str(edge["to"]), str(edge.get("edgeType", "")))


def compose_repository_documents(documents: tuple[dict, ...]) -> ComposedGraph:
    datasets_by_repo: dict[str, set[str]] = {}
    edges_by_repo: dict[str, int] = {}
    # One logical edge may be asserted by several repositories. Merging them here is
    # what turns "the same fact found twice" into "one fact with two witnesses" —
    # the shape the product needs to blend signals rather than double-count them.
    merged: dict[tuple[str, str, str], dict] = {}

    for document in documents:
        repo = str(document.get("repo", ""))
        document_edges = list(document.get("edges", ()))
        datasets = datasets_by_repo.setdefault(repo, set())
        edges_by_repo[repo] = edges_by_repo.get(repo, 0) + len(document_edges)
        for edge in document_edges:
            datasets.add(_dataset_of(str(edge["to"])))
            for source in edge.get("from", ()):
                datasets.add(_dataset_of(str(source)))

            key = _edge_key(edge)
            record = merged.get(key)
            if record is None:
                record = {
                    **edge,
                    "contributedBy": set(),
                    "provenanceIds": set(),
                    "transforms": set(),
                }
                merged[key] = record
            record["contributedBy"].add(repo)
            if edge.get("provenanceId"):
                record["provenanceIds"].add(str(edge["provenanceId"]))
            if edge.get("transform") is not None:
                record["transforms"].add(str(edge["transform"]))

    edges: list[dict] = []
    for record in merged.values():
        transforms = sorted(record["transforms"])
        edges.append(
            {
                **{
                    key: value
                    for key, value in record.items()
                    if key not in {"contributedBy", "provenanceIds", "transforms"}
                },
                "contributedBy": sorted(record["contributedBy"]),
                "provenanceIds": sorted(record["provenanceIds"]),
                "transforms": transforms,
                "transformConflict": len(transforms) > 1,
            }
        )

    contributions = tuple(
        RepositoryContribution(
            repo=repo,
            edge_count=edges_by_repo[repo],
            datasets=tuple(sorted(datasets_by_repo[repo])),
        )
        for repo in sorted(datasets_by_repo)
    )

    every_dataset = {name for names in datasets_by_repo.values() for name in names}
    shared = tuple(
        sorted(
            dataset
            for dataset in every_dataset
            if sum(1 for names in datasets_by_repo.values() if dataset in names) > 1
        )
    )

    return ComposedGraph(
        edges=tuple(
            sorted(edges, key=lambda edge: (str(edge["to"]), str(edge["from"][0])))
        ),
        contributions=contributions,
        shared_datasets=shared,
    )
