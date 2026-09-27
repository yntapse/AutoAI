from etl_copilot.join_graph import build_fk_graph
from etl_copilot.spec_models import Catalog, JoinHop

catalog = Catalog(
    tables=[],
    relationships=[
        JoinHop(
            left_table="orders",
            left_column="customer_id",
            right_table="customers",
            right_column="customer_id",
        )
    ],
)

print(build_fk_graph(catalog))
