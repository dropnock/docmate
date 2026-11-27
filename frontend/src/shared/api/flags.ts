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
  /** Set while the flag is shared with the customer's supervisors. */
  shared_at: string | null;
  shared_by: number | null;
}

/** A row of the supervisor's flag list (GET /projects/{id}/flags). */
export interface RecordFlagListItem extends RecordFlag {
  record_filename: string | null;
  source_identifier: string | null;
  record_status: string;
  batch_name: string | null;
  raised_by_name: string;
  closed_by_name: string | null;
  shared_by_name: string | null;
  comment_count: number;
}

export interface FlagComment {
  id: number;
  flag_id: number;
  author_id: number;
  author_name: string;
  author_side: "digitizing" | "customer";
  body: string;
  created_at: string;
}

export interface RecordFlagState {
  open: RecordFlag | null;
  last_resolved: RecordFlag | null;
}

export const recordFlagKey = (recordId: number) => ["record-flag", recordId] as const;
export const projectFlagsKey = (projectId: number, status: FlagStatus, q?: string) =>
  (q ? ["project-flags", projectId, status, q] : ["project-flags", projectId, status]) as readonly unknown[];
export const flagKey = (flagId: number) => ["flag", flagId] as const;
export const flagCommentsKey = (flagId: number) => ["flag-comments", flagId] as const;

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

/** q: only flags whose record, batch, issue, resolution or discussion
 * contains it (or whose record/flag id it is). */
export function listProjectFlags(projectId: number, status: FlagStatus, q?: string) {
  return api
    .get<RecordFlagListItem[]>(`/projects/${projectId}/flags`, { params: { status, q: q || undefined } })
    .then((r) => r.data);
}

/** Downloads the flag list (one tab: open / resolved / withdrawn) as a CSV file. */
export async function exportProjectFlags(projectId: number, status: FlagStatus) {
  const res = await api.get<Blob>(`/projects/${projectId}/flags/export`, { params: { status }, responseType: "blob" });
  const match = /filename="?([^";]+)"?/.exec(res.headers["content-disposition"] ?? "");
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? `flagged_records_${status}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function resolveFlag(flagId: number, resolution: string) {
  return api.post<RecordFlag>(`/flags/${flagId}/resolve`, { resolution }).then((r) => r.data);
}

/** One flag as a list row — a notification's link target. */
export function getFlag(flagId: number) {
  return api.get<RecordFlagListItem>(`/flags/${flagId}`).then((r) => r.data);
}

/** Makes the selected open flags visible to the customer's supervisors. */
export function shareFlags(projectId: number, flagIds: number[]) {
  return api.post<RecordFlag[]>(`/projects/${projectId}/flags/share`, { flag_ids: flagIds }).then((r) => r.data);
}

export function unshareFlag(flagId: number) {
  return api.post<RecordFlag>(`/flags/${flagId}/unshare`).then((r) => r.data);
}

export function listFlagComments(flagId: number) {
  return api.get<FlagComment[]>(`/flags/${flagId}/comments`).then((r) => r.data);
}

export function addFlagComment(flagId: number, body: string) {
  return api.post<FlagComment>(`/flags/${flagId}/comments`, { body }).then((r) => r.data);
}
