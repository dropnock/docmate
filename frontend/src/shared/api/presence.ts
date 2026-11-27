import api from "./client";
import type { OnlineUser } from "@shared/types";

export const onlineUsersKey = (within: number) => ["users-online", within] as const;

export function listOnlineUsers(within: number): Promise<OnlineUser[]> {
  return api.get("/users/online", { params: { within } }).then((r) => r.data);
}
