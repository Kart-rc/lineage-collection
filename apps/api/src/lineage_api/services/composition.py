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


def compose_repository_documents(documents: tuple[dict, ...]) -> ComposedGraph:
    edges: list[dict] = []
    datasets_by_repo: dict[str, set[str]] = {}
    edges_by_repo: dict[str, int] = {}

    for document in documents:
        repo = str(document.get("repo", ""))
        document_edges = list(document.get("edges", ()))
        datasets = datasets_by_repo.setdefault(repo, set())
        edges_by_repo[repo] = edges_by_repo.get(repo, 0) + len(document_edges)
        for edge in document_edges:
            datasets.add(_dataset_of(str(edge["to"])))
            for source in edge.get("from", ()):
                datasets.add(_dataset_of(str(source)))
        edges.extend(document_edges)

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
