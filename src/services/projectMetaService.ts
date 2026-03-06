const API_BASE = "http://localhost:8000";

export interface ProjectMetaResponse {
  project_id: string;
  file_id: string;
  project_name: string;
  target_column: string;
  num_rows: number;
}

export async function getProjectMetaByFileId(fileId: string): Promise<ProjectMetaResponse> {
  const response = await fetch(`${API_BASE}/projects/by-file/${encodeURIComponent(fileId)}`, {
    cache: "no-store",
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
        : "Failed to fetch project metadata.";

    throw new Error(message);
  }

  return payload as ProjectMetaResponse;
}
