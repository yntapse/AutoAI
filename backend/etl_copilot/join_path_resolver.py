from collections import deque


class JoinPathResolver:

    def resolve(
        self,
        joins,
        entity_table,
        source_table,
    ):

        if entity_table == source_table:
            return []

        graph = {}

        for join in joins:

            graph.setdefault(join.left_table, []).append(join)

        queue = deque(
            [
                (
                    entity_table,
                    [],
                )
            ]
        )

        visited = set()

        while queue:

            current_table, path = queue.popleft()

            if current_table == source_table:
                return path

            if current_table in visited:
                continue

            visited.add(current_table)

            for join in graph.get(current_table, []):

                queue.append(
                    (
                        join.right_table,
                        path + [join],
                    )
                )

        return []
