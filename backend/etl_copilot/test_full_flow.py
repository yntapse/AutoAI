from etl_copilot.copilot_service import ETLCopilot

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
        "OrderItems": [
            "OrderItemID",
            "OrderID",
            "ProductID",
            "Quantity",
        ],
    }
}


copilot = ETLCopilot()

result = copilot.create_dataset(
    "Show customer name, city, total sales, order count and total quantity",
    schema,
)

print("\nSPEC:\n")
print(result["spec"])

print("\nSQL:\n")
print(result["sql"])
