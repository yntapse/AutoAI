const API_BASE = "http://localhost:8000";

export async function createProject(
  projectName: string,
  targetColumn: string,
  file: File
): Promise<unknown> {
  const formData = new FormData();
  formData.append("project_name", projectName);
  formData.append("target_column", targetColumn);
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/projects/create`, {
    method: "POST",
    body: formData,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to create project.";

    throw new Error(message);
  }

  return payload;
}
