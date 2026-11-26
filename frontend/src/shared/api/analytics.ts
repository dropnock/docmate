import api from "./client";
import type { StaffTaskSort, StaffTaskTimes, StaffTaskType } from "@shared/types";

export interface StaffTaskTimesParams {
  projectId: number;
  userId: number;
  taskType: StaffTaskType;
  dateFrom?: string;
  dateTo?: string;
  sort: StaffTaskSort;
  page: number;
  pageSize: number;
}

function query(p: StaffTaskTimesParams) {
  return {
    project_id: p.projectId,
    user_id: p.userId,
    task_type: p.taskType,
    date_from: p.dateFrom,
    date_to: p.dateTo,
    sort: p.sort,
    page: p.page,
    page_size: p.pageSize,
  };
}

export const staffTaskTimesKey = (p: StaffTaskTimesParams) => ["staff-task-times", p] as const;

export function getStaffTaskTimes(p: StaffTaskTimesParams) {
  return api.get<StaffTaskTimes>("/analytics/staff-productivity/tasks", { params: query(p) }).then((r) => r.data);
}

/** Every matching row as a CSV download. */
export async function exportStaffTaskTimes(p: StaffTaskTimesParams) {
  const res = await api.get<Blob>("/analytics/staff-productivity/tasks", {
    params: { ...query(p), format: "csv", page: undefined, page_size: undefined },
    responseType: "blob",
  });
  const match = /filename="?([^";]+)"?/.exec(res.headers["content-disposition"] ?? "");
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? `${p.taskType}-times.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
