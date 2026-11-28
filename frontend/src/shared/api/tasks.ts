import api from "@shared/api/client";
import type { FieldResult, Task } from "@shared/types";

export function completeTask(taskId: number, body: { indexed_data?: null; field_results?: FieldResult[] }) {
  return api.post<Task>(`/tasks/${taskId}/complete`, body);
}

export function failTask(taskId: number, body: { reason: string; field_results?: FieldResult[] }) {
  return api.post<Task>(`/tasks/${taskId}/fail`, body);
}

/** The field marks the reviewer gave on a QC task (for going back to it). */
export function getTaskFieldResults(taskId: number) {
  return api.get<FieldResult[]>(`/tasks/${taskId}/field-results`).then((r) => r.data);
}

/** The customer QC reviewer submits their QC batch once every record is
 * reviewed; after that its records can't be reviewed again. */
export function submitQcBatch(batchId: number) {
  return api.post(`/batches/${batchId}/submit-qc`).then((r) => r.data);
}
