from etl_copilot.schema_reader import SchemaReader
from services.db_connector import build_connection_string

reader = SchemaReader()

password = input("Enter SQL Server password: ").strip()

connection_string = build_connection_string(
    db_type="mssql",
    host="localhost",
    database="AutoMLDB",
    port=53199,
    username="automl",
    password=password,
    trust_server_certificate=True,
)

schema = reader.get_schema(connection_string=connection_string)

print(schema)
