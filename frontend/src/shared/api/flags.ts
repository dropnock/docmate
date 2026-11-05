import api from "@shared/api/client";

export type FlagStatus = "open" | "resolved" | "withdrawn";

export interface RecordFlag {
  id: number;
  record_id: number;
  batch_id: number | null;
  project_id: number;
  status: FlagStatus;
  reason: string;
  raised_by: number;
  raised_at: string;
  closed_by: number | null;
  closed_at: string | null;
  resolution: string | null;
}

/** A row of the supervisor's flag list (GET /projects/{id}/flags). */
export interface RecordFlagListItem extends RecordFlag {
  record_filename: string | null;
  record_status: string;
  batch_name: string | null;
  raised_by_name: string;
  closed_by_name: string | null;
}

export interface RecordFlagState {
  open: RecordFlag | null;
  last_resolved: RecordFlag | null;
}

export const recordFlagKey = (recordId: number) => ["record-flag", recordId] as const;
export const projectFlagsKey = (projectId: number, status: FlagStatus) => ["project-flags", projectId, status] as const;

export function getRecordFlag(recordId: number) {
  return api.get<RecordFlagState>(`/records/${recordId}/flag`).then((r) => r.data);
}

/** Flags the record, or updates the note on its open flag. */
export function raiseRecordFlag(recordId: number, reason: string) {
  return api.put<RecordFlag>(`/records/${recordId}/flag`, { reason }).then((r) => r.data);
}

export function withdrawRecordFlag(recordId: number) {
  return api.delete<RecordFlag>(`/records/${recordId}/flag`).then((r) => r.data);
}

export function listProjectFlags(projectId: number, status: FlagStatus) {
  return api.get<RecordFlagListItem[]>(`/projects/${projectId}/flags`, { params: { status } }).then((r) => r.data);
}

export function resolveFlag(flagId: number, resolution: string) {
  return api.post<RecordFlag>(`/flags/${flagId}/resolve`, { resolution }).then((r) => r.data);
}
