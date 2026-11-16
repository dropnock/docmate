import api from "@shared/api/client";

export interface RecordHold {
  id: number;
  record_id: number;
  project_id: number;
  reason: string;
  held_by: number;
  held_at: string;
  released_by: number | null;
  released_at: string | null;
  release_note: string | null;
}

/** A row of GET /projects/{id}/holds. */
export interface RecordHoldListItem extends RecordHold {
  record_filename: string | null;
  source_identifier: string | null;
  record_status: string;
  held_by_name: string;
  released_by_name: string | null;
}

export const projectHoldsKey = (projectId: number) => ["project-holds", projectId] as const;

export function listHolds(projectId: number) {
  return api.get<RecordHoldListItem[]>(`/projects/${projectId}/holds`).then((r) => r.data);
}

export function holdRecords(projectId: number, recordIds: number[], reason: string) {
  return api.post<RecordHold[]>(`/projects/${projectId}/holds`, { record_ids: recordIds, reason }).then((r) => r.data);
}

export function releaseHolds(projectId: number, recordIds: number[], note?: string) {
  return api
    .post<RecordHold[]>(`/projects/${projectId}/holds/release`, { record_ids: recordIds, note: note || null })
    .then((r) => r.data);
}
