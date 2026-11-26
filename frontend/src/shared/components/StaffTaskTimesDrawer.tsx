import { useState } from "react";
import { Alert, Button, DatePicker, Drawer, Space, Statistic, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnType, SorterResult } from "antd/es/table/interface";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";
import { exportStaffTaskTimes, getStaffTaskTimes, staffTaskTimesKey, type StaffTaskTimesParams } from "@shared/api/analytics";
import { formatApiError } from "@shared/api/errors";
import type { StaffTaskSort, StaffTaskTime, StaffTaskType } from "@shared/types";

export function fmtDuration(secs: number | null | undefined): string {
  if (!secs) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h) return `${h}h ${m}m`;
  return m ? `${m}m ${s}s` : `${s}s`;
}

const TYPE_LABEL: Record<StaffTaskType, string> = { indexing: "Indexing", qa: "QA", qc: "QC" };

function Outcome({ outcome }: { outcome: string }) {
  if (outcome.startsWith("skipped_")) return <Tag>Skipped · {outcome.slice(8)}</Tag>;
  const map: Record<string, [string, string | undefined]> = {
    submitted: ["Submitted", "green"],
    passed: ["Passed", "green"],
    rejected: ["Rejected", "red"],
    requeued: ["Requeued", "orange"],
    in_progress: ["In progress", "blue"],
  };
  const [label, color] = map[outcome] ?? [outcome, undefined];
  return <Tag color={color}>{label}</Tag>;
}

const fmtWhen = (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—");

interface Props {
  open: boolean;
  onClose: () => void;
  projectId: number;
  agent: { user_id: number; full_name: string } | null;
  taskType: StaffTaskType;
  /** The report's selected day; the drawer starts on it. */
  date?: string;
}

/** One agent's time on each record: the detail behind the report's Avg Time.
 * Time adds up across every session the record was open and leaves out time
 * away from it (backend services/task_timing.py). */
export default function StaffTaskTimesDrawer({ open, onClose, projectId, agent, taskType, date }: Props) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={960}
      destroyOnClose
      title={agent ? `${agent.full_name} · ${TYPE_LABEL[taskType]} time per record` : ""}
    >
      {agent && <Body projectId={projectId} userId={agent.user_id} taskType={taskType} date={date} />}
    </Drawer>
  );
}

function Body({ projectId, userId, taskType, date }: { projectId: number; userId: number; taskType: StaffTaskType; date?: string }) {
  const start = date ? dayjs(date) : dayjs();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>([start, start]);
  const [sort, setSort] = useState<StaffTaskSort>("time_desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);

  const params: StaffTaskTimesParams = {
    projectId, userId, taskType, sort, page, pageSize,
    dateFrom: range?.[0].format("YYYY-MM-DD"),
    dateTo: range?.[1].format("YYYY-MM-DD"),
  };
  const { data, isFetching } = useQuery({
    queryKey: staffTaskTimesKey(params),
    queryFn: () => getStaffTaskTimes(params),
    placeholderData: keepPreviousData,
  });
  const s = data?.summary;
  const legacy = data?.rows.some((r) => r.sessions === 0 && r.status !== "in_progress");

  // Never back to unsorted: antd steps to the entry after the current one.
  const flip = ["descend", "ascend", "descend"] as ("descend" | "ascend")[];
  const sortOrder = (col: "time" | "finished") =>
    sort === `${col}_desc` ? ("descend" as const) : sort === `${col}_asc` ? ("ascend" as const) : null;

  const columns: ColumnType<StaffTaskTime>[] = [
    { title: "Record", dataIndex: "record_label", render: (v: string, r) => <Tooltip title={`Record #${r.record_id}`}>{v}</Tooltip> },
    { title: "Batch", dataIndex: "batch_name", ellipsis: true },
    { title: "Started", dataIndex: "started_at", width: 140, render: fmtWhen },
    { title: "Finished", key: "finished", dataIndex: "finished_at", width: 140, render: fmtWhen, sorter: true, sortDirections: flip, sortOrder: sortOrder("finished") },
    {
      title: "Time",
      key: "time",
      dataIndex: "seconds",
      width: 100,
      sorter: true,
      sortDirections: flip,
      sortOrder: sortOrder("time"),
      render: (v: number, r) => (
        <span data-task-seconds={v}>
          {fmtDuration(v)}
          {r.status === "in_progress" && <Typography.Text type="secondary"> so far</Typography.Text>}
        </span>
      ),
    },
    {
      title: <Tooltip title="Times the record was opened. Time away between sessions isn't counted.">Sessions</Tooltip>,
      dataIndex: "sessions",
      width: 90,
      render: (n: number) => (n === 0 ? <Tooltip title="Timed before sessions were counted: last open to finish">—</Tooltip> : n > 1 ? <Tag color="gold">{n}</Tag> : n),
    },
    { title: "Outcome", dataIndex: "outcome", width: 140, render: (o: string) => <Outcome outcome={o} /> },
  ];

  const onTableChange = (pg: { current?: number; pageSize?: number }, _f: unknown, sorter: SorterResult<StaffTaskTime> | SorterResult<StaffTaskTime>[]) => {
    const one = Array.isArray(sorter) ? sorter[0] : sorter;
    const col = one?.columnKey === "finished" ? "finished" : "time";
    const next: StaffTaskSort = one?.order ? `${col}_${one.order === "ascend" ? "asc" : "desc"}` : "time_desc";
    if (next !== sort) {
      setSort(next);
      setPage(1);
    } else {
      setPage(pg.current ?? 1);
    }
    setPageSize(pg.pageSize ?? 50);
  };

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }} wrap>
        <Space wrap>
          <DatePicker.RangePicker
            value={range}
            allowClear
            onChange={(v) => {
              setRange(v && v[0] && v[1] ? [v[0], v[1]] : null);
              setPage(1);
            }}
            placeholder={["All time", "All time"]}
          />
          <Button size="small" onClick={() => { setRange(null); setPage(1); }} disabled={!range}>
            All time
          </Button>
        </Space>
        <Button
          loading={exporting}
          disabled={!data?.total}
          onClick={async () => {
            setExporting(true);
            try {
              await exportStaffTaskTimes(params);
            } catch (e) {
              message.error(formatApiError(e, "Could not download the CSV"));
            } finally {
              setExporting(false);
            }
          }}
        >
          Download CSV
        </Button>
      </Space>

      <Space size={32} wrap style={{ marginBottom: 16 }} data-task-summary>
        <Statistic title="Records finished" value={s?.finished ?? 0} />
        <Statistic title="Total time" value={fmtDuration(s?.total_seconds)} />
        <Statistic title="Average" value={fmtDuration(s?.avg_seconds)} />
        <Statistic title="Median" value={fmtDuration(s?.median_seconds)} />
        <Statistic title="Fastest" value={fmtDuration(s?.fastest_seconds)} />
        <Statistic title="Slowest" value={fmtDuration(s?.slowest_seconds)} />
        <Statistic title={<Tooltip title="Records opened more than once">Reopened</Tooltip>} value={s?.reopened ?? 0} />
      </Space>

      {legacy && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="Records with — under Sessions were timed before sessions were counted: from the last time they were opened to when they were finished."
        />
      )}

      <Table
        rowKey="task_id"
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={data?.rows ?? []}
        onChange={onTableChange}
        pagination={{ current: page, pageSize, total: data?.total ?? 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
      />
    </>
  );
}
