import api from "@shared/api/client";

export type NotificationKind = "flag_shared" | "flag_unshared" | "flag_comment" | "flag_resolved";

export interface AppNotification {
  id: number;
  kind: NotificationKind;
  project_id: number | null;
  flag_id: number | null;
  record_id: number | null;
  actor_id: number | null;
  message: string;
  created_at: string;
  read_at: string | null;
}

export const unreadCountKey = ["notifications", "unread-count"] as const;
export const notificationsKey = ["notifications", "list"] as const;

export function getUnreadCount() {
  return api.get<{ count: number }>("/notifications/unread-count").then((r) => r.data.count);
}

export function listNotifications() {
  return api.get<AppNotification[]>("/notifications").then((r) => r.data);
}

/** Marks the given notifications read, or all of them when ids is omitted. */
export function markNotificationsRead(ids?: number[]) {
  return api.post<{ marked: number }>("/notifications/read", ids ? { ids } : {}).then((r) => r.data);
}
