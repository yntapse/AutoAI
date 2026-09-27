from etl_copilot.join_discovery import JoinDiscovery

schema = {
    "tables": {
        "Customers": [
            "CustomerID",
            "CustomerName",
            "City",
        ],
        "Orders": [
            "OrderID",
            "CustomerID",
            "OrderAmount",
        ],
    }
}

finder = JoinDiscovery()

joins = finder.discover(schema)

for join in joins:
    print(join)
