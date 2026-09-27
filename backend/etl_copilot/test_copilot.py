from etl_copilot.copilot_service import ETLCopilot

copilot = ETLCopilot()

result = copilot.create_spec_from_request(
    "Build customer dataset with total sales and order count"
)

print(result)
