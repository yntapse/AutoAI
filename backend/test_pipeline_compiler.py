"""Visual ETL Pipeline Builder - graph rules and SQL compilation.

No server is needed. The "customer database" is a temporary SQLite file and
db_connector's engine factory is pointed at it, so the PostgreSQL-style SQL the
compiler writes is actually EXECUTED and its rows checked. SQL Server SQL cannot
run on SQLite, so for SQL Server the tests check the generated text only. A
live SQL Server and a live PostgreSQL are still needed for a final check.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine

from services import db_connector
from services import pipeline_compiler as pc
from services.db_connector import run_preview_query

# SQLite only learned RIGHT and FULL joins in 3.39. On older builds those two are checked
# as SQL text only (the report says so); LEFT and INNER are always executed.
CAN_RUN_OUTER = sqlite3.sqlite_version_info >= (3, 39, 0)

PG = "postgresql"
MS = "mssql"


def cols(*pairs):
    return [pc.make_column(n, t) for n, t in pairs]


CUSTOMERS = pc.SourceTable(
    "t-cust", "main", "customers",
    cols(("customer_id", "INTEGER"), ("full_name", "TEXT"), ("region", "TEXT"), ("age", "INTEGER"), ("score", "REAL"), ("joined", "DATE")),
    row_count=6, row_count_is_estimate=False,
)
ORDERS = pc.SourceTable(
    "t-ord", "main", "orders",
    cols(("order_id", "INTEGER"), ("customer_id", "INTEGER"), ("amount", "REAL"), ("status", "TEXT")),
)
WEIRD = pc.SourceTable(
    "t-weird", None, "we]ird",
    cols(("a:b", "INTEGER"), ('q"uote', "TEXT")),
)
SOURCES = {s.table_id: s for s in (CUSTOMERS, ORDERS, WEIRD)}


def build_db(path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE customers(customer_id INTEGER, full_name TEXT, region TEXT, age INTEGER, score REAL, joined DATE);
        INSERT INTO customers VALUES
          (1,'Asha','West',30,0.5,'2020-01-05'),
          (2,'Bala','East',NULL,1.5,'2021-03-10'),
          (3,'Chitra','West',41,NULL,'2022-07-01'),
          (4,'Dev','North',25,2.0,NULL),
          (5,'Asha','West',30,0.5,'2020-01-05'),
          (NULL,'Ghost','East',50,3.0,'2023-01-01');
        CREATE TABLE orders(order_id INTEGER, customer_id INTEGER, amount REAL, status TEXT);
        INSERT INTO orders VALUES
          (10,1,100.0,'paid'),(11,1,50.0,'paid'),(12,2,75.0,'open'),
          (13,2,20.0,'100% off'),(14,9,5.0,'paid'),(15,NULL,1.0,'paid');
        CREATE TABLE "we]ird"("a:b" INTEGER, "q""uote" TEXT);
        INSERT INTO "we]ird" VALUES (1,'x'),(2,'y');
        """
    )
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        build_db(self.path)
        p = patch.object(db_connector, "_make_engine", lambda cs: create_engine(f"sqlite:///{self.path}"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    # helpers -----------------------------------------------------------
    def compile(self, nodes, edges, db=PG, sources=None):
        return pc.compile_pipeline(db, nodes, edges, sources or SOURCES)

    def rows(self, compiled, node_id, limit=100):
        sql, params = pc.build_preview_sql(compiled, node_id, limit)
        names, data = run_preview_query(f"postgresql://x/y", sql, params, max_rows=limit + 1)
        return names, data

    def issues(self, compiled, node_id, kind=None):
        return [i.message for i in compiled.results[node_id].issues if kind is None or i.kind == kind]


def src(nid, table_id):
    return {"id": nid, "type": "source", "config": {"table_id": table_id}}


def edge(s, t, port="in"):
    return {"source": s, "target": t, "port": port}


class GraphRules(Base):
    def test_valid_chain_is_ready_with_columns(self):
        c = self.compile([src("a", "t-cust"), {"id": "o", "type": "output", "config": {}}], [edge("a", "o")])
        self.assertEqual(c.results["o"].status, "ready")
        self.assertEqual([x.name for x in c.results["o"].columns][:2], ["customer_id", "full_name"])

    def test_missing_input_is_not_configured_and_has_no_sql(self):
        c = self.compile([{"id": "o", "type": "output", "config": {}}], [])
        self.assertEqual(c.results["o"].status, "not_configured")
        self.assertIsNone(c.results["o"].sql)

    def test_source_without_a_table_is_not_configured(self):
        c = self.compile([{"id": "a", "type": "source", "config": {}}], [])
        self.assertEqual(c.results["a"].status, "not_configured")

    def test_unknown_table_id_is_invalid_not_trusted(self):
        c = self.compile([src("a", "no-such-table")], [])
        self.assertEqual(c.results["a"].status, "invalid")

    def test_downstream_of_a_problem_is_blocked_and_cannot_be_previewed(self):
        nodes = [src("a", "t-cust"), {"id": "f", "type": "filter", "config": {"conditions": [{"column": "nope", "operator": "eq", "value": 1}]}},
                 {"id": "o", "type": "output", "config": {}}]
        c = self.compile(nodes, [edge("a", "f"), edge("f", "o")])
        self.assertEqual(c.results["f"].status, "invalid")
        self.assertEqual(c.results["o"].status, "blocked")
        with self.assertRaises(pc.PipelineError):
            pc.build_preview_sql(c, "o", 10)

    def test_a_loop_is_rejected(self):
        nodes = [{"id": "x", "type": "filter", "config": {}}, {"id": "y", "type": "filter", "config": {}}]
        with self.assertRaises(pc.PipelineError):
            self.compile(nodes, [edge("x", "y"), edge("y", "x")])

    def test_duplicate_ids_bad_ids_and_unknown_nodes_are_rejected(self):
        with self.assertRaises(pc.PipelineError):
            self.compile([src("a", "t-cust"), src("a", "t-ord")], [])
        with self.assertRaises(pc.PipelineError):
            self.compile([src("a b", "t-cust")], [])
        with self.assertRaises(pc.PipelineError):
            self.compile([src("a", "t-cust")], [edge("a", "ghost")])
        with self.assertRaises(pc.PipelineError):
            self.compile([src("a", "t-cust")], [edge("a", "a")])

    def test_unknown_node_type_is_invalid(self):
        c = self.compile([{"id": "z", "type": "teleport", "config": {}}], [])
        self.assertEqual(c.results["z"].status, "invalid")

    def test_second_connection_to_the_same_input_is_invalid(self):
        nodes = [src("a", "t-cust"), src("b", "t-ord"), {"id": "o", "type": "output", "config": {}}]
        c = self.compile(nodes, [edge("a", "o"), edge("b", "o")])
        self.assertEqual(c.results["o"].status, "invalid")

    def test_too_many_nodes(self):
        with self.assertRaises(pc.PipelineError):
            self.compile([src(f"n{i}", "t-cust") for i in range(pc.MAX_NODES + 1)], [])

    def test_unsupported_database_type(self):
        with self.assertRaises(pc.PipelineError):
            pc.compile_pipeline("oracle", [], [], {})

    def test_can_connect_rules(self):
        nodes = [src("a", "t-cust"), src("b", "t-ord"), {"id": "j", "type": "join", "config": {}}, {"id": "o", "type": "output", "config": {}}]
        edges = [edge("a", "j", "left")]
        self.assertIsNone(pc.can_connect(nodes, edges, "b", "j", "right"))
        self.assertIn("already connected", pc.can_connect(nodes, edges, "b", "j", "left"))
        self.assertIn("inputs are", pc.can_connect(nodes, edges, "b", "j", "in"))
        self.assertIn("takes no input", pc.can_connect(nodes, edges, "j", "a", "in"))
        self.assertIn("nothing after", pc.can_connect(nodes, edges, "o", "j", "right"))
        self.assertIn("itself", pc.can_connect(nodes, edges, "j", "j", "left"))
        chain = [edge("a", "j", "left"), edge("j", "o")]
        two = [*nodes, {"id": "f", "type": "filter", "config": {}}, {"id": "g", "type": "filter", "config": {}}]
        self.assertIn("loop", pc.can_connect(two, [edge("f", "g")], "g", "f", "in"))
        self.assertIsNone(pc.can_connect(nodes, chain, "b", "j", "right"))


class Joins(Base):
    def join_nodes(self, join_type="INNER", conditions=None):
        return [src("c", "t-cust"), src("o", "t-ord"),
                {"id": "j", "type": "join", "config": {
                    "join_type": join_type,
                    "conditions": conditions if conditions is not None else [{"left_column": "customer_id", "right_column": "customer_id"}]}}]

    def run_join(self, join_type):
        c = self.compile(self.join_nodes(join_type), [edge("c", "j", "left"), edge("o", "j", "right")])
        self.assertEqual(c.results["j"].status, "ready", self.issues(c, "j"))
        return c, self.rows(c, "j")

    def test_inner_left_right_full_row_counts(self):
        # customers: ids 1,2,3,4,5,NULL.  orders: customer ids 1,1,2,2,9,NULL.
        expected = {"INNER": 4, "LEFT": 4 + 4, "RIGHT": 4 + 2, "FULL": 4 + 4 + 2}
        for jt, n in expected.items():
            if jt in ("RIGHT", "FULL") and not CAN_RUN_OUTER:
                c = self.compile(self.join_nodes(jt), [edge("c", "j", "left"), edge("o", "j", "right")])
                self.assertIn(f"{jt} OUTER JOIN", pc.build_preview_sql(c, "j", 10)[0])
                continue
            _, (names, data) = self.run_join(jt)
            self.assertEqual(len(data), n, jt)

    def test_collision_is_renamed_and_reported(self):
        c, (names, _) = self.run_join("INNER")
        self.assertIn("customer_id", names)
        self.assertIn("customer_id_right", names)
        self.assertTrue(any("renamed" in n for n in c.results["j"].notes))

    def test_multiple_conditions(self):
        conds = [{"left_column": "customer_id", "right_column": "customer_id"}, {"left_column": "region", "right_column": "status"}]
        c = self.compile(self.join_nodes("INNER", conds), [edge("c", "j", "left"), edge("o", "j", "right")])
        self.assertEqual(c.results["j"].status, "ready")
        _, data = self.rows(c, "j")
        self.assertEqual(len(data), 0)  # region never equals status

    def test_incompatible_types_are_warned_not_hidden(self):
        conds = [{"left_column": "customer_id", "right_column": "status"}]
        c = self.compile(self.join_nodes("INNER", conds), [edge("c", "j", "left"), edge("o", "j", "right")])
        self.assertEqual(c.results["j"].status, "ready")
        self.assertTrue(any("different types" in m for m in self.issues(c, "j", "warning")))

    def test_bad_join_configuration(self):
        e = [edge("c", "j", "left"), edge("o", "j", "right")]
        for conds, kind in [([], "incomplete"), ([{"left_column": "nope", "right_column": "customer_id"}], "invalid"),
                            ([{"left_column": "customer_id", "right_column": "nope"}], "invalid"),
                            ([{"left_column": "customer_id", "right_column": ""}], "incomplete"),
                            ([{"left_column": "customer_id", "right_column": "customer_id"}] * 2, "invalid"),
                            ([{"left_column": "customer_id", "right_column": "customer_id"}] * 6, "invalid")]:
            c = self.compile(self.join_nodes("INNER", conds), e)
            self.assertIn(kind, {i.kind for i in c.results["j"].issues}, conds)
            self.assertIsNone(c.results["j"].sql)
        c = self.compile(self.join_nodes("CROSS"), e)
        self.assertEqual(c.results["j"].status, "invalid")
        c = self.compile(self.join_nodes(""), e)
        self.assertEqual(c.results["j"].status, "not_configured")

    def test_join_needs_both_inputs(self):
        c = self.compile(self.join_nodes(), [edge("c", "j", "left")])
        self.assertEqual(c.results["j"].status, "not_configured")

    def test_diagnostics_match_a_real_run_for_every_join_type(self):
        nodes = self.join_nodes("LEFT")
        c = self.compile(nodes, [edge("c", "j", "left"), edge("o", "j", "right")])
        sql, params, _ = pc.build_join_diagnostics_sql(c, "j", nodes[2])
        names, data = run_preview_query("postgresql://x/y", sql, params, max_rows=5)
        row = dict(zip(names, data[0]))
        for jt in pc.JOIN_TYPES:
            if jt in ("RIGHT", "FULL") and not CAN_RUN_OUTER:
                continue
            summary = pc.summarise_join(row, jt)
            cj = self.compile(self.join_nodes(jt), [edge("c", "j", "left"), edge("o", "j", "right")])
            _, actual = self.rows(cj, "j")
            self.assertEqual(summary["output_rows"], len(actual), jt)
        s = pc.summarise_join(row, "LEFT")
        self.assertEqual((s["left_rows"], s["right_rows"]), (6, 6))
        self.assertEqual(s["matched_pairs"], 4)
        self.assertEqual(s["unmatched_left_rows"], 6 - 2)       # ids 3,4,5 and NULL have no order
        self.assertEqual(s["unmatched_right_rows"], 6 - 4)      # customer 9 and NULL
        self.assertEqual(s["left_null_keys"], 1)
        self.assertEqual(s["right_null_keys"], 1)
        self.assertEqual(s["largest_right_key_group"], 2)
        self.assertTrue(s["multiplies_left_rows"])              # customers 1 and 2 each match two orders
        self.assertFalse(s["multiplies_right_rows"])
        self.assertEqual(s["extra_rows_from_multiplication"], 2)
        self.assertTrue(s["multiplies_main_side"])
        self.assertEqual(s["main_side"], "left")
        # For a RIGHT join the main side is the right table, which is not inflated here.
        r = pc.summarise_join(row, "RIGHT")
        self.assertEqual(r["main_side"], "right")
        self.assertFalse(r["multiplies_main_side"])
        self.assertEqual(r["extra_rows_from_multiplication"], 0)

    def test_diagnostics_only_for_joins(self):
        c = self.compile(self.join_nodes(), [edge("c", "j", "left"), edge("o", "j", "right")])
        with self.assertRaises(pc.PipelineError):
            pc.build_join_diagnostics_sql(c, "c", {})


class Transforms(Base):
    def chain(self, node, source="t-cust", extra=None):
        nodes = [src("s", source), {"id": "t", **node}, {"id": "o", "type": "output", "config": {}}]
        edges = [edge("s", "t"), edge("t", "o")]
        return self.compile(nodes, edges)

    def exec_node(self, node, source="t-cust"):
        c = self.chain(node, source)
        self.assertEqual(c.results["t"].status, "ready", self.issues(c, "t"))
        return self.rows(c, "o")

    def f(self, *conds, combine="AND"):
        return {"type": "filter", "config": {"combine": combine, "conditions": [dict(column=a, operator=b, value=v) for a, b, v in conds]}}

    # filter ------------------------------------------------------------
    def test_filter_operators(self):
        for cond, n in [(("age", "gt", 29), 4), (("age", "gte", 30), 4), (("age", "lt", 30), 1), (("age", "lte", 30), 3),
                        (("age", "eq", 30), 2), (("age", "ne", 30), 3), (("age", "is_null", None), 1), (("age", "is_not_null", None), 5),
                        (("full_name", "contains", "sh"), 2), (("full_name", "starts_with", "As"), 2), (("full_name", "ends_with", "a"), 4),
                        (("region", "eq", "West"), 3), (("joined", "gte", "2021-01-01"), 3)]:
            _, data = self.exec_node(self.f(cond))
            self.assertEqual(len(data), n, cond)

    def test_filter_and_or(self):
        _, a = self.exec_node(self.f(("region", "eq", "West"), ("age", "gt", 35)))
        _, o = self.exec_node(self.f(("region", "eq", "North"), ("age", "gt", 45), combine="OR"))
        self.assertEqual((len(a), len(o)), (1, 2))

    def test_filter_validation(self):
        bad = [self.f(("nope", "eq", 1)), self.f(("age", "contains", "x")), self.f(("age", "eq", "abc")), self.f(("age", "eq", 1.5)),
               self.f(("joined", "gt", "yesterday")), self.f(("age", "eq", True)), self.f(("age", "eq", float("nan"))),
               {"type": "filter", "config": {"combine": "XOR", "conditions": [dict(column="age", operator="eq", value=1)]}},
               {"type": "filter", "config": {"conditions": [dict(column="age", operator="between", value=1)]}},
               self.f(*[("age", "eq", i) for i in range(11)])]
        for node in bad:
            c = self.chain(node)
            self.assertEqual(c.results["t"].status, "invalid", node)
        c = self.chain({"type": "filter", "config": {"conditions": []}})
        self.assertEqual(c.results["t"].status, "not_configured")
        c = self.chain(self.f(("age", "eq", None)))
        self.assertEqual(c.results["t"].status, "not_configured")

    def test_values_are_parameters_never_sql_text(self):
        evil = "x'; DROP TABLE customers; --"
        c = self.chain(self.f(("full_name", "eq", evil)))
        sql = c.results["t"].sql
        self.assertNotIn("DROP", sql)
        self.assertNotIn(evil, sql)
        self.assertIn(evil, c.params.values())
        _, data = self.rows(c, "o")
        self.assertEqual(data, [])
        _, still = run_preview_query("postgresql://x/y", "SELECT * FROM customers", None, max_rows=100)
        self.assertEqual(len(still), 6)

    def test_like_wildcards_in_values_are_literal(self):
        _, data = self.exec_node(self.f(("status", "contains", "100%")), source="t-ord")
        self.assertEqual(len(data), 1)
        _, none = self.exec_node(self.f(("status", "contains", "%")), source="t-ord")
        self.assertEqual(len(none), 1)  # only the row that really contains a percent sign
        _, under = self.exec_node(self.f(("status", "contains", "_")), source="t-ord")
        self.assertEqual(under, [])

    # drop / rename ------------------------------------------------------
    def test_drop_columns(self):
        names, data = self.exec_node({"type": "drop_columns", "config": {"columns": ["score", "joined"]}})
        self.assertEqual(names, ["customer_id", "full_name", "region", "age"])
        self.assertEqual(len(data), 6)

    def test_drop_columns_validation(self):
        for cfg, kind in [({"columns": []}, "not_configured"), ({"columns": ["nope"]}, "invalid"), ({"columns": ["age", "age"]}, "invalid"),
                          ({"columns": [c.name for c in CUSTOMERS.columns]}, "invalid")]:
            self.assertEqual(self.chain({"type": "drop_columns", "config": cfg}).results["t"].status, kind, cfg)

    def test_rename_columns(self):
        names, _ = self.exec_node({"type": "rename_columns", "config": {"mappings": [{"from": "full_name", "to": "name"}, {"from": "age", "to": "age in years"}]}})
        self.assertIn("name", names)
        self.assertIn("age in years", names)
        self.assertNotIn("full_name", names)

    def test_rename_validation(self):
        bad = [[{"from": "age", "to": "region"}], [{"from": "age", "to": "REGION"}], [{"from": "age", "to": ""}], [{"from": "age", "to": " x"}],
               [{"from": "age", "to": "a\nb"}], [{"from": "age", "to": "x" * 129}], [{"from": "nope", "to": "x"}],
               [{"from": "age", "to": "x"}, {"from": "age", "to": "y"}]]
        for m in bad:
            self.assertIn(self.chain({"type": "rename_columns", "config": {"mappings": m}}).results["t"].status, ("invalid", "not_configured"), m)
        # swapping two names is allowed because the final names are unique
        names, _ = self.exec_node({"type": "rename_columns", "config": {"mappings": [{"from": "region", "to": "full_name"}, {"from": "full_name", "to": "region"}]}})
        self.assertEqual(names[1:3], ["region", "full_name"])

    def test_awkward_identifiers_are_quoted(self):
        c = self.compile([src("s", "t-weird"), {"id": "r", "type": "rename_columns", "config": {"mappings": [{"from": "a:b", "to": "c]d"}]}}],
                         [edge("s", "r")])
        self.assertEqual(c.results["r"].status, "ready")
        names, data = self.rows(c, "r")
        self.assertEqual(names, ["c]d", 'q"uote'])
        self.assertEqual(len(data), 2)

    # duplicates ---------------------------------------------------------
    def test_remove_duplicates_all_columns(self):
        # Rows 1 and 5 are the same person but have different ids, so drop the id first.
        nodes = [src("s", "t-cust"), {"id": "d", "type": "drop_columns", "config": {"columns": ["customer_id"]}},
                 {"id": "t", "type": "remove_duplicates", "config": {}}]
        c = self.compile(nodes, [edge("s", "d"), edge("d", "t")])
        self.assertEqual(c.results["t"].status, "ready", self.issues(c, "t"))
        _, data = self.rows(c, "t")
        self.assertEqual(len(data), 5)  # the two identical Asha rows collapse

    def test_remove_duplicates_by_key_keeps_one_row_per_key(self):
        c = self.chain({"type": "remove_duplicates", "config": {"columns": ["region"]}})
        self.assertEqual(c.results["t"].status, "ready")
        self.assertTrue(any("not defined" in m for m in self.issues(c, "t", "warning")))
        names, data = self.rows(c, "o")
        self.assertEqual(len(data), 3)
        self.assertEqual(names, [x.name for x in CUSTOMERS.columns])  # helper column does not leak

    def test_remove_duplicates_validation(self):
        for cols_ in (["nope"], ["age", "age"], "age"):
            self.assertEqual(self.chain({"type": "remove_duplicates", "config": {"columns": cols_}}).results["t"].status, "invalid")
        legacy = pc.SourceTable("t-leg", None, "legacy", cols(("id", "INTEGER"), ("notes", "text")))
        legacy_nodes = [src("s", "t-leg"), {"id": "t", "type": "remove_duplicates", "config": {}}]
        # SQL Server's legacy TEXT cannot be compared; PostgreSQL's TEXT can.
        c = pc.compile_pipeline(MS, legacy_nodes, [edge("s", "t")], {"t-leg": legacy})
        self.assertEqual(c.results["t"].status, "invalid")
        c = pc.compile_pipeline(PG, legacy_nodes, [edge("s", "t")], {"t-leg": legacy})
        self.assertEqual(c.results["t"].status, "ready")

    # missing values -----------------------------------------------------
    def test_missing_drop_rows(self):
        _, data = self.exec_node({"type": "handle_missing", "config": {"strategy": "drop_rows", "columns": ["age", "score"]}})
        self.assertEqual(len(data), 4)

    def test_missing_fixed_value(self):
        names, data = self.exec_node({"type": "handle_missing", "config": {"strategy": "fixed", "columns": ["age"], "value": 0}})
        ages = [r[names.index("age")] for r in data]
        self.assertNotIn(None, ages)
        self.assertEqual(sorted(ages), [0, 25, 30, 30, 41, 50])
        names, data = self.exec_node({"type": "handle_missing", "config": {"strategy": "fixed", "columns": ["region"], "value": "?"}})
        self.assertEqual(len(data), 6)

    def test_missing_mean(self):
        c = self.chain({"type": "handle_missing", "config": {"strategy": "mean", "columns": ["age"]}})
        self.assertEqual(c.results["t"].status, "ready")
        self.assertTrue(any(x.name == "age" and x.category == "numeric" and x.data_type == "double precision" for x in c.results["t"].columns))
        names, data = self.rows(c, "o")
        ages = [r[names.index("age")] for r in data]
        mean = (30 + 41 + 25 + 30 + 50) / 5
        self.assertAlmostEqual(ages[1], mean)
        self.assertNotIn(None, ages)
        c_ms = self.compile([src("s", "t-cust"), {"id": "t", "type": "handle_missing", "config": {"strategy": "mean", "columns": ["age"]}}], [edge("s", "t")], db=MS)
        self.assertIn("CAST([age] AS FLOAT)", c_ms.results["t"].sql)

    def test_missing_validation(self):
        bad = [({"strategy": "mean", "columns": ["region"]}, "invalid"), ({"strategy": "median", "columns": ["age"]}, "invalid"),
               ({"strategy": "fixed", "columns": ["age"], "value": 1.5}, "invalid"), ({"strategy": "fixed", "columns": ["age"], "value": "x"}, "invalid"),
               ({"strategy": "fixed", "columns": ["age"]}, "not_configured"), ({"columns": ["age"]}, "not_configured"),
               ({"strategy": "drop_rows", "columns": []}, "not_configured"), ({"strategy": "drop_rows", "columns": ["nope"]}, "invalid"),
               ({"strategy": "drop_rows", "columns": ["age", "age"]}, "invalid")]
        for cfg, kind in bad:
            self.assertEqual(self.chain({"type": "handle_missing", "config": cfg}).results["t"].status, kind, cfg)

    def test_a_pipeline_of_several_steps(self):
        nodes = [src("c", "t-cust"), src("o", "t-ord"),
                 {"id": "j", "type": "join", "config": {"join_type": "LEFT", "conditions": [{"left_column": "customer_id", "right_column": "customer_id"}]}},
                 {"id": "f", "type": "filter", "config": {"conditions": [{"column": "region", "operator": "eq", "value": "West"}]}},
                 {"id": "d", "type": "drop_columns", "config": {"columns": ["score", "joined", "customer_id_right"]}},
                 {"id": "m", "type": "handle_missing", "config": {"strategy": "fixed", "columns": ["amount"], "value": 0}},
                 {"id": "r", "type": "rename_columns", "config": {"mappings": [{"from": "full_name", "to": "customer"}]}},
                 {"id": "u", "type": "remove_duplicates", "config": {}},
                 {"id": "out", "type": "output", "config": {"name": "customer_orders"}}]
        edges = [edge("c", "j", "left"), edge("o", "j", "right"), edge("j", "f"), edge("f", "d"), edge("d", "m"), edge("m", "r"), edge("r", "u"), edge("u", "out")]
        c = self.compile(nodes, edges)
        for n in nodes:
            self.assertEqual(c.results[n["id"]].status, "ready", (n["id"], self.issues(c, n["id"])))
        names, data = self.rows(c, "out")
        self.assertEqual(names, ["customer_id", "customer", "region", "age", "order_id", "amount", "status"])
        # West customers 1 (two orders), 5 (none), 3 (none). Asha(1) and Asha(5) differ by customer_id.
        self.assertEqual(len(data), 4)
        self.assertTrue(all(r[names.index("amount")] is not None for r in data))
        # an intermediate node can be previewed too, and only needs its own ancestors
        sql, _ = pc.build_preview_sql(c, "j", 5)
        self.assertNotIn("DISTINCT", sql)


class SqlShape(Base):
    def test_preview_is_capped_in_the_database(self):
        nodes = [src("s", "t-cust")]
        for db, marker in ((PG, "LIMIT 11"), (MS, "SELECT TOP 11")):
            c = self.compile(nodes, [], db=db)
            sql, _ = pc.build_preview_sql(c, "s", 10)
            self.assertIn(marker, sql)
            self.assertTrue(sql.lstrip().upper().startswith("WITH"))
            self.assertNotIn(";", sql)

    def test_preview_limit_bounds(self):
        c = self.compile([src("s", "t-cust")], [])
        for bad in (0, -1, 101, "5", None, True, 5.5):
            with self.assertRaises(pc.PipelineError):
                pc.build_preview_sql(c, "s", bad)
        pc.build_preview_sql(c, "s", 1)
        pc.build_preview_sql(c, "s", 100)

    def test_preview_returns_at_most_limit_plus_one_rows(self):
        c = self.compile([src("s", "t-cust")], [])
        _, data = self.rows(c, "s", limit=3)
        self.assertEqual(len(data), 4)

    def test_only_needed_parameters_are_sent(self):
        nodes = [src("a", "t-cust"), {"id": "f1", "type": "filter", "config": {"conditions": [{"column": "age", "operator": "gt", "value": 1}]}},
                 src("b", "t-ord"), {"id": "f2", "type": "filter", "config": {"conditions": [{"column": "amount", "operator": "gt", "value": 2}]}}]
        c = self.compile(nodes, [edge("a", "f1"), edge("b", "f2")])
        self.assertEqual(len(c.params), 2)
        _, params = pc.build_preview_sql(c, "f1", 5)
        self.assertEqual(list(params.values()), [1])

    def test_unknown_node_cannot_be_previewed(self):
        c = self.compile([src("s", "t-cust")], [])
        with self.assertRaises(pc.PipelineError):
            pc.build_preview_sql(c, "ghost", 5)

    def test_sql_server_identifiers_use_brackets_and_postgres_uses_quotes(self):
        c_ms = self.compile([src("s", "t-weird")], [], db=MS)
        c_pg = self.compile([src("s", "t-weird")], [], db=PG)
        self.assertIn("[we]]ird]", c_ms.results["s"].sql)
        self.assertIn('"we]ird"', c_pg.results["s"].sql)
        self.assertIn('[q"uote]', c_ms.results["s"].sql)
        self.assertIn('"q""uote"', c_pg.results["s"].sql)

    def test_display_sql_removes_the_colon_escape(self):
        c = self.compile([src("s", "t-weird")], [])
        sql, _ = pc.build_preview_sql(c, "s", 5)
        self.assertIn("\\:", sql)
        self.assertNotIn("\\:", pc.display_sql(sql))

    def test_the_compiler_never_modifies_data(self):
        c = self.compile([src("s", "t-cust"), {"id": "d", "type": "remove_duplicates", "config": {}}], [edge("s", "d")])
        sql, _ = pc.build_preview_sql(c, "d", 5)
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "EXEC"):
            self.assertNotIn(word, sql.upper().replace("OUTER", ""))

    def test_database_errors_are_reported_as_data_source_errors(self):
        c = self.compile([src("s", "t-ord")], [], sources={**SOURCES, "t-ord": pc.SourceTable("t-ord", "main", "missing_table", ORDERS.columns)})
        sql, params = pc.build_preview_sql(c, "s", 5)
        with self.assertRaises(db_connector.DataSourceError):
            run_preview_query("postgresql://x/y", sql, params, max_rows=6)


if __name__ == "__main__":
    unittest.main()
