import { useState } from "react";
import { Badge, Button, Empty, List, Popover, Space, Typography } from "antd";
import { Bell } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import dayjs from "dayjs";
import {
  getUnreadCount, listNotifications, markNotificationsRead, notificationsKey, unreadCountKey,
  type AppNotification,
} from "@shared/api/notifications";

/** Where a notification leads: the Flagged Records page of its project,
 * opened on the flag when there is one. Both portals use the same path. */
function linkFor(n: AppNotification): string | null {
  if (!n.project_id) return null;
  const params = new URLSearchParams({ project: String(n.project_id) });
  if (n.flag_id) params.set("flag", String(n.flag_id));
  return `/flagged-records?${params}`;
}

/** Header bell for both portals: unread count, polled; the latest
 * notifications in a popover; clicking one marks it read and opens it. */
export default function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const { data: unread = 0 } = useQuery({
    queryKey: unreadCountKey,
    queryFn: getUnreadCount,
    refetchInterval: 30_000,
  });
  const { data: notes = [], isLoading } = useQuery({
    queryKey: notificationsKey,
    queryFn: listNotifications,
    enabled: open,
  });

  const markRead = useMutation({
    mutationFn: (ids?: number[]) => markNotificationsRead(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const openNote = (n: AppNotification) => {
    if (!n.read_at) markRead.mutate([n.id]);
    const link = linkFor(n);
    setOpen(false);
    if (link) navigate(link);
  };

  const content = (
    <div style={{ width: 360, maxWidth: "calc(100vw - 32px)" }}>
      <List
        size="small"
        loading={isLoading}
        dataSource={notes}
        style={{ maxHeight: 420, overflowY: "auto" }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No notifications" /> }}
        renderItem={(n) => (
          <List.Item
            onClick={() => openNote(n)}
            style={{ cursor: "pointer", background: n.read_at ? undefined : "#E8EEF6", paddingInline: 8 }}
          >
            <Space direction="vertical" size={2} style={{ width: "100%" }}>
              <Typography.Text strong={!n.read_at}>{n.message}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(n.created_at).format("D MMM YYYY, HH:mm")}
              </Typography.Text>
            </Space>
          </List.Item>
        )}
      />
    </div>
  );

  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
      title={
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <span>Notifications</span>
          <Button size="small" type="link" disabled={!unread} onClick={() => markRead.mutate(undefined)}>
            Mark all read
          </Button>
        </Space>
      }
      content={content}
    >
      <Button
        type="text"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        icon={
          <Badge count={unread} size="small" overflowCount={99}>
            <Bell size={18} />
          </Badge>
        }
      />
    </Popover>
  );
}
