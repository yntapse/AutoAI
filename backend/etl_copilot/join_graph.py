"""Deterministic join-path discovery over declared foreign keys.

Pure module: no database connection, no pandas, no LLM. Takes a Catalog,
returns JoinPath objects and ValidationIssue objects.

DESIGN RULES (from the architecture document):

  * Foreign keys are the source of truth. Column-name matching is NEVER
    used here - a shared column name does not prove a relationship.
  * Paths are found by BFS, so the shortest path wins.
  * Several paths of equal length are AMBIGUOUS and must be resolved by the
    user. This module never picks one silently.
  * No path is a validation issue, not an exception.
  * More than MAX_HOPS hops is a validation issue.
  * verified is ALWAYS False here. Only join_verifier may set it True.
"""

from collections import deque
from typing import Dict, List, Optional, Tuple

from etl_copilot.spec_models import (
    Catalog,
    JoinHop,
    JoinPath,
    ValidationIssue,
)

MAX_HOPS = 3


def build_fk_graph(catalog: Catalog) -> Dict[str, List[Tuple[str, str, str]]]:
    """Undirected adjacency built from declared foreign keys.

    {
        "Orders": [("Customers", "CustomerID", "CustomerID"), ...],
        "Customers": [("Orders", "CustomerID", "CustomerID"), ...],
    }

    Each entry is (neighbour_table, column_on_this_table, column_on_neighbour).
    Both directions are stored so traversal is direction-agnostic; the emitted
    JoinHop always reads left -> right along the direction of travel.
    """

    graph: Dict[str, List[Tuple[str, str, str]]] = {}

    for rel in catalog.relationships:
        graph.setdefault(rel.left_table, []).append(
            (rel.right_table, rel.left_column, rel.right_column)
        )
        graph.setdefault(rel.right_table, []).append(
            (rel.left_table, rel.right_column, rel.left_column)
        )

    return graph


def find_all_shortest_paths(
    catalog: Catalog,
    start_table: str,
    end_table: str,
) -> List[List[JoinHop]]:
    """Every shortest path from start to end, as lists of JoinHop.

    Level-by-level BFS. The visited set is applied PER LEVEL, not globally,
    so two distinct routes of the same length are both discovered - which is
    what makes ambiguity detectable. A global visited set (marked at enqueue
    time) would silently return only the first route found.
    """

    if start_table == end_table:
        return [[]]

    graph = build_fk_graph(catalog)

    if start_table not in graph and end_table not in graph:
        return []

    # Each frontier entry: (table, hops_so_far, tables_on_this_route)
    frontier: List[Tuple[str, List[JoinHop], set]] = [
        (start_table, [], {start_table})
    ]
    settled: set = {start_table}
    found: List[List[JoinHop]] = []

    for _ in range(MAX_HOPS + 1):
        if found or not frontier:
            break

        next_frontier: List[Tuple[str, List[JoinHop], set]] = []
        reached_this_level: set = set()

        for table, hops, route in frontier:
            for neighbour, left_column, right_column in graph.get(table, []):
                # A route may not revisit its own tables (cycle guard),
                # and may not step back onto an already-settled level.
                if neighbour in route or neighbour in settled:
                    continue

                hop = JoinHop(
                    left_table=table,
                    left_column=left_column,
                    right_table=neighbour,
                    right_column=right_column,
                )

                extended = hops + [hop]

                if neighbour == end_table:
                    found.append(extended)
                    continue

                reached_this_level.add(neighbour)
                next_frontier.append(
                    (neighbour, extended, route | {neighbour})
                )

        settled |= reached_this_level
        frontier = next_frontier

    return found


def resolve_join_path(
    catalog: Catalog,
    entity_table: str,
    source_table: str,
) -> Tuple[Optional[JoinPath], List[ValidationIssue]]:
    """Resolve exactly one deterministic path, or explain why it cannot be.

    Returns (path_or_None, issues). verified is always False on the returned
    path - only join_verifier may set it True.
    """

    issues: List[ValidationIssue] = []

    known_tables = {table.name for table in catalog.tables}

    for name, field in ((entity_table, "entity_table"), (source_table, "source_table")):
        if known_tables and name not in known_tables:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="unknown_table",
                    message="Table '%s' is not in the catalog." % name,
                    field=field,
                    suggestion="Select the table, or check the schema name.",
                )
            )

    if issues:
        return None, issues

    if entity_table == source_table:
        return (
            JoinPath(
                entity_table=entity_table,
                source_table=source_table,
                hops=[],
                verified=True,  # nothing to join, nothing to measure
                discovered_via="entity_table",
                alternatives_considered=1,
            ),
            issues,
        )

    paths = find_all_shortest_paths(catalog, entity_table, source_table)

    if not paths:
        issues.append(
            ValidationIssue(
                severity="error",
                code="no_join_path",
                message=(
                    "No declared foreign-key path connects '%s' to '%s'."
                    % (entity_table, source_table)
                ),
                field="join_paths",
                suggestion=(
                    "Declare the relationship manually, or add the foreign "
                    "key in the database. Column-name matching is not "
                    "accepted as proof of a relationship."
                ),
            )
        )
        return None, issues

    if len(paths) > 1:
        routes = [
            " -> ".join([entity_table] + [hop.right_table for hop in path])
            for path in paths
        ]
        issues.append(
            ValidationIssue(
                severity="error",
                code="ambiguous_join_path",
                message=(
                    "%d equally short join paths connect '%s' to '%s': %s"
                    % (len(paths), entity_table, source_table, "; ".join(routes))
                ),
                field="join_paths",
                suggestion="Choose which path to use; these return different numbers.",
            )
        )
        return None, issues

    hops = paths[0]

    if len(hops) > MAX_HOPS:
        issues.append(
            ValidationIssue(
                severity="error",
                code="join_path_too_long",
                message=(
                    "The shortest path from '%s' to '%s' needs %d hops "
                    "(maximum %d)." % (entity_table, source_table, len(hops), MAX_HOPS)
                ),
                field="join_paths",
                suggestion="Pick a nearer source table or declare a direct relationship.",
            )
        )
        return None, issues

    return (
        JoinPath(
            entity_table=entity_table,
            source_table=source_table,
            hops=hops,
            verified=False,
            discovered_via="foreign_key",
            alternatives_considered=len(paths),
        ),
        issues,
    )


def resolve_paths_for_spec(catalog: Catalog, spec) -> Tuple[List[JoinPath], List[ValidationIssue]]:
    """One path per distinct measure/attribute source table."""

    issues: List[ValidationIssue] = []
    paths: List[JoinPath] = []
    seen: set = set()

    sources = [measure.source_table for measure in spec.measures]
    sources += [attribute.source_table for attribute in spec.attributes]

    for source_table in sources:
        if source_table in seen:
            continue
        seen.add(source_table)

        path, path_issues = resolve_join_path(
            catalog, spec.entity_table, source_table
        )
        issues.extend(path_issues)

        if path is not None:
            paths.append(path)

    return paths, issues
