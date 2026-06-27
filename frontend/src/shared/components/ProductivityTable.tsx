import { Badge, Table, Tag, Typography } from "antd";
import type { ColumnType } from "antd/es/table";
import type { StaffMetric } from "@shared/types";

function fmtTime(secs: number) {
  if (!secs) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

interface Props {
  data: StaffMetric[];
  loading: boolean;
}

const columns: ColumnType<StaffMetric>[] = [
  { title: "Name", dataIndex: "full_name", key: "name" },
  { title: "Role", dataIndex: "role", key: "role", render: (r) => <Tag>{r}</Tag> },
  { title: "Today", dataIndex: "records_today", key: "today", sorter: (a, b) => a.records_today - b.records_today },
  { title: "Total", dataIndex: "total_records_processed", key: "total", sorter: (a, b) => a.total_records_processed - b.total_records_processed },
  { title: "Avg Time", dataIndex: "avg_processing_time_seconds", key: "avg", render: fmtTime },
  {
    title: "Error Rate",
    dataIndex: "error_rate",
    key: "error",
    render: (r) => {
      const pct = (r * 100).toFixed(1);
      return <span style={{ color: r > 0.05 ? "red" : "inherit" }}>{pct}%</span>;
    },
  },
  {
    title: "Stale",
    dataIndex: "stale_task_count",
    key: "stale",
    render: (n) => n > 0 ? <Badge count={n} /> : "—",
  },
  {
    title: "In Progress",
    dataIndex: "tasks_in_progress",
    key: "inprog",
    render: (n) => n > 0 ? <Tag color="processing">{n}</Tag> : "—",
  },
];

export default function ProductivityTable({ data, loading }: Props) {
  return (
    <Table
      rowKey="user_id"
      columns={columns}
      dataSource={data}
      loading={loading}
      size="middle"
      pagination={{ pageSize: 20 }}
    />
  );
}
