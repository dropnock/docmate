import { useState } from "react";
import { Button, Table, Tag, Tooltip } from "antd";
import type { ColumnType } from "antd/es/table";
import type { StaffTaskType, TaskTypeMetrics } from "@shared/types";
import StaffTaskTimesDrawer from "./StaffTaskTimesDrawer";

function fmtTime(secs: number) {
  if (!secs) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

interface Props<T extends StaffTaskType> {
  data: Array<{ user_id: number; full_name: string; email: string } & Record<T, TaskTypeMetrics>>;
  loading: boolean;
  taskType: T;
  projectId: number;
  /** The report's selected day: the per-record view opens on it. */
  date?: string;
}

type Row = { user_id: number; full_name: string; email: string } & TaskTypeMetrics;

const baseColumns: ColumnType<Row>[] = [
  { title: "Today", dataIndex: "records_today", key: "today", sorter: (a, b) => a.records_today - b.records_today },
  { title: "Total", dataIndex: "total_records_processed", key: "total", sorter: (a, b) => a.total_records_processed - b.total_records_processed },
  { title: "Avg Time", dataIndex: "avg_processing_time_seconds", key: "avg", render: fmtTime },
  {
    title: "Error Rate",
    dataIndex: "error_rate",
    key: "error",
    render: (r) => {
      const pct = (r * 100).toFixed(1);
      return <span style={{ color: "#0F172A", fontWeight: r > 0.05 ? 700 : 400 }}>{pct}%</span>;
    },
  },
  {
    title: "In Progress",
    dataIndex: "tasks_in_progress",
    key: "inprog",
    render: (n) =>
      n > 0 ? (
        <Tag style={{ color: "#1E40AF", background: "#EFF6FF", border: "1px solid #1E40AF" }}>{n}</Tag>
      ) : (
        "—"
      ),
  },
];

/** Per-agent metrics; an agent's name (or Avg Time) opens their time on each
 * record. */
export default function ProductivityTable<T extends StaffTaskType>({ data, loading, taskType, projectId, date }: Props<T>) {
  const [agent, setAgent] = useState<Row | null>(null);
  const columns: ColumnType<Row>[] = [
    {
      title: "Name",
      dataIndex: "full_name",
      key: "name",
      render: (v: string, r) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => setAgent(r)} data-agent-detail={r.user_id}>
          {v}
        </Button>
      ),
    },
    ...baseColumns.map((c) =>
      c.key === "avg"
        ? {
            ...c,
            render: (secs: number, r: Row) => (
              <Tooltip title="Time per record">
                <a onClick={() => setAgent(r)}>{fmtTime(secs)}</a>
              </Tooltip>
            ),
          }
        : c,
    ),
  ];
  const rows: Row[] = data.map((d) => ({
    user_id: d.user_id,
    full_name: d.full_name,
    email: d.email,
    ...d[taskType],
  }));

  return (
    <>
      <Table
        rowKey="user_id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        size="middle"
        pagination={{ defaultPageSize: 20 }}
      />
      <StaffTaskTimesDrawer
        open={!!agent}
        onClose={() => setAgent(null)}
        projectId={projectId}
        agent={agent}
        taskType={taskType}
        date={date}
      />
    </>
  );
}
