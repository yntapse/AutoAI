import io
import pandas as pd
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def post_csv(csv_text: str, name: str = "test.csv"):
    files = {"file": (name, csv_text.encode("utf-8"), "text/csv")}
    data = {"project_name": "Limit Test", "target_column": "target"}
    return client.post("/projects/create", data=data, files=files)


# 1) Large rows (> 50,000)
df_large_rows = pd.DataFrame({"feature": list(range(50001)), "target": [1.0] * 50001})
rows_buf = io.StringIO()
df_large_rows.to_csv(rows_buf, index=False)
res_rows = post_csv(rows_buf.getvalue(), "large_rows.csv")

# 2) Large columns (> 200)
columns_data = {f"c{i}": [i] for i in range(200)}
columns_data["target"] = [1.0]
columns_data["extra_col"] = [999]
df_large_cols = pd.DataFrame(columns_data)
cols_buf = io.StringIO()
df_large_cols.to_csv(cols_buf, index=False)
res_cols = post_csv(cols_buf.getvalue(), "large_columns.csv")

# 3) Large file size (> 20MB)
long_text = "x" * 800
df_large_file = pd.DataFrame({"feature": [long_text] * 30000, "target": [1.0] * 30000})
file_buf = io.StringIO()
df_large_file.to_csv(file_buf, index=False)
large_file_csv = file_buf.getvalue()
file_size_mb = len(large_file_csv.encode("utf-8")) / (1024 * 1024)
res_file = post_csv(large_file_csv, "large_file.csv")

print("Large rows ->", res_rows.status_code, res_rows.json())
print("Large columns ->", res_cols.status_code, res_cols.json())
print("Large file size (MB) ->", round(file_size_mb, 2))
print("Large file ->", res_file.status_code, res_file.json())
