import api from "@shared/api/client";
import type { FieldResult, Task } from "@shared/types";

export function completeTask(taskId: number, body: { indexed_data?: null; field_results?: FieldResult[] }) {
  return api.post<Task>(`/tasks/${taskId}/complete`, body);
}

export function failTask(taskId: number, body: { reason: string; field_results?: FieldResult[] }) {
  return api.post<Task>(`/tasks/${taskId}/fail`, body);
}
