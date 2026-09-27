from collections import deque
from typing import Dict, List, Tuple

from .spec_models import Catalog


class AmbiguousJoinPathError(Exception):
    pass


class NoJoinPathError(Exception):
    pass


def build_fk_graph(catalog: Catalog) -> Dict[str, List[Tuple[str, str, str]]]:
    """
    Graph format:
    {
        "orders": [
            ("customers", "customer_id", "customer_id")
        ]
    }
    """

    graph: Dict[str, List[Tuple[str, str, str]]] = {}

    for rel in catalog.relationships:
        graph.setdefault(rel.from_table, []).append(
            (rel.right_table, rel.left_column, rel.right_column)
        )

        graph.setdefault(rel.right_table, []).append(
            (rel.left_table, rel.right_column, rel.left_column)
        )

    return graph


def find_shortest_path(
    catalog: Catalog,
    start_table: str,
    end_table: str,
):
    if start_table == end_table:
        return []

    graph = build_fk_graph(catalog)

    queue = deque([(start_table, [])])
    visited = {start_table}

    found_paths = []

    while queue:
        table, path = queue.popleft()

        if table == end_table:
            found_paths.append(path)
            continue

        for next_table, left_col, right_col in graph.get(table, []):
            if next_table not in visited:
                visited.add(next_table)

                queue.append(
                    (
                        next_table,
                        path
                        + [
                            {
                                "left_table": table,
                                "right_table": next_table,
                                "left_column": left_col,
                                "right_column": right_col,
                            }
                        ],
                    )
                )

    if not found_paths:
        raise NoJoinPathError(f"No join path between {start_table} and {end_table}")

    if len(found_paths) > 1:
        raise AmbiguousJoinPathError(
            f"Multiple join paths between {start_table} and {end_table}"
        )

    return found_paths[0]
