import { Badge, Table, Tag } from "antd";
import type { ColumnType } from "antd/es/table";
import type { StaffMetric, TaskTypeMetrics } from "@shared/types";

function fmtTime(secs: number) {
  if (!secs) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

interface Props {
  data: StaffMetric[];
  loading: boolean;
  taskType: "indexing" | "qa";
}

type Row = { user_id: number; full_name: string; email: string } & TaskTypeMetrics;

const columns: ColumnType<Row>[] = [
  { title: "Name", dataIndex: "full_name", key: "name" },
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
    title: "Stale",
    dataIndex: "stale_task_count",
    key: "stale",
    render: (n) => n > 0 ? <Badge count={n} color="#1E40AF" /> : "—",
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

export default function ProductivityTable({ data, loading, taskType }: Props) {
  const rows: Row[] = data.map((d) => ({
    user_id: d.user_id,
    full_name: d.full_name,
    email: d.email,
    ...d[taskType],
  }));

  return (
    <Table
      rowKey="user_id"
      columns={columns}
      dataSource={rows}
      loading={loading}
      size="middle"
      pagination={{ pageSize: 20 }}
    />
  );
}
