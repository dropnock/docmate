import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Card, Empty, Segmented, Space, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import PageHeader from "@shared/components/PageHeader";
import { listOnlineUsers, onlineUsersKey } from "@shared/api/presence";
import type { OnlineUser } from "@shared/types";

const WINDOWS = [
  { value: 15, label: "Last 15 min" },
  { value: 60, label: "Last hour" },
  { value: 480, label: "Last 8 hours" },
];

const TASK_LABEL = { indexing: "Indexing", qa: "QA", qc: "Customer QC" } as const;

/** "just now", "4 min ago", "2 h 5 min ago". */
function ago(iso: string | null, now: number): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min ago`;
}

function duration(iso: string | null, now: number): string {
  if (!iso) return "—";
  const m = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function Presence({ u }: { u: OnlineUser }) {
  const working = u.task?.working;
  const color = u.status === "active" ? (working ? "#16A34A" : "#1E40AF") : "#94A3B8";
  const label = u.status === "active" ? (working ? "Working" : "Online") : "Idle";
  return (
    <Tooltip
      title={
        working
          ? "Typing or clicking in a record in the last 90 seconds"
          : u.status === "active"
            ? "Has DocMate open (a request in the last 2 minutes)"
            : "No requests for over 2 minutes: tab closed, asleep or signed out"
      }
    >
      <Space size={6} data-presence={label.toLowerCase()}>
        <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: color }} />
        {label}
      </Space>
    </Tooltip>
  );
}

/** Admin: who has DocMate open, in either portal, and what they're working on. */
export default function WhosOnline() {
  const [within, setWithin] = useState(15);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, []);

  const { data: users = [], isLoading, dataUpdatedAt } = useQuery({
    queryKey: onlineUsersKey(within),
    queryFn: () => listOnlineUsers(within),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

  const active = users.filter((u) => u.status === "active");
  const working = active.filter((u) => u.task?.working);
  const customers = active.filter((u) => u.portal === "customer");

  const columns: ColumnsType<OnlineUser> = [
    {
      title: "Status",
      key: "status",
      width: 110,
      render: (_, u) => <Presence u={u} />,
    },
    {
      title: "Name",
      key: "name",
      render: (_, u) => (
        <div>
          <Typography.Text strong>{u.full_name}</Typography.Text>
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{u.email}</Typography.Text>
        </div>
      ),
    },
    {
      title: "Role",
      key: "role",
      render: (_, u) => (
        <Space size={4} wrap>
          <Tag>{u.role.replace(/_/g, " ")}</Tag>
          {u.portal === "customer" && <Tag color="purple">{u.organization_name ?? "Customer"}</Tag>}
        </Space>
      ),
    },
    {
      title: "Working on",
      key: "task",
      render: (_, u) =>
        u.task ? (
          <Tooltip title={`${u.task.project_name} · ${u.task.batch_name} — task #${u.task.id}`}>
            <div data-online-task={u.task.id}>
              <Tag>{TASK_LABEL[u.task.task_type]}</Tag>
              <Typography.Text>{u.task.record_name}</Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {u.task.batch_name.split(" — ")[0]} · last input {ago(u.task.last_active_at, now)}
              </Typography.Text>
            </div>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: "Last seen",
      dataIndex: "last_seen_at",
      width: 140,
      render: (v: string) => <Tooltip title={dayjs(v).format("YYYY-MM-DD HH:mm:ss")}>{ago(v, now)}</Tooltip>,
    },
    {
      title: "Online for",
      dataIndex: "session_started_at",
      width: 130,
      render: (v: string | null) =>
        v ? <Tooltip title={`Since ${dayjs(v).format("YYYY-MM-DD HH:mm")}`}>{duration(v, now)}</Tooltip> : "—",
    },
  ];

  return (
    <>
      <PageHeader
        title="Who's Online"
        description="Everyone signed in to either portal. Last seen is the last time their browser talked to DocMate; an open tab keeps checking in, so Online means DocMate is open, and Working means they've typed or clicked in a record in the last 90 seconds. Refreshes every 30 seconds."
        extra={
          <Segmented
            value={within}
            onChange={(v) => setWithin(v as number)}
            options={WINDOWS}
            data-online-window
          />
        }
      />
      <Space size={32} style={{ marginBottom: 16 }} wrap>
        <Statistic title="Online now" value={active.length} />
        <Statistic title="Working on a record" value={working.length} />
        <Statistic title="Customer users online" value={customers.length} />
      </Space>
      <Card>
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={users}
          pagination={{ defaultPageSize: 50, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="Nobody in this time window" /> }}
        />
        {dataUpdatedAt > 0 && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Updated {dayjs(dataUpdatedAt).format("HH:mm:ss")}
          </Typography.Text>
        )}
      </Card>
    </>
  );
}
