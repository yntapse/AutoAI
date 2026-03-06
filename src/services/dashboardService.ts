import { apiFetch } from "@/lib/api";

export interface DashboardProject {
  project_id: string;
  file_id: string;
  project_name: string;
  status: "Completed" | "Training" | "Failed" | "Pending";
  accuracy_percent: number | null;
  num_rows: number;
  created_at: string | null;
  target_column: string;
}

export interface DashboardOverviewResponse {
  summary: {
    total_projects: number;
    completed_projects: number;
    running_projects: number;
  };
  projects: DashboardProject[];
}

export async function getDashboardOverview(): Promise<DashboardOverviewResponse> {
  return apiFetch<DashboardOverviewResponse>("/dashboard/overview", {
    cache: "no-store",
  });
}

export async function deleteProject(projectId: string): Promise<void> {
  await apiFetch<{ message: string; project_id: string }>(`/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
}
