import { useState } from "react";
import { Alert, Button, Card, Empty, Input, Segmented, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { formatApiError } from "@shared/api/errors";
import {
  listProjectFlags, projectFlagsKey, resolveFlag, type FlagStatus, type RecordFlagListItem,
} from "@shared/api/flags";
import RecordViewerDrawer from "@shared/components/RecordViewerDrawer";

const when = (iso: string | null) => (iso ? dayjs(iso).format("D MMM YYYY, HH:mm") : "—");

/** Supervisor's queue of records indexers have flagged. Review opens the
 * record (image + data) with the indexer's note; resolving records what was
 * done and removes the flag. The record stays in its batch either way. */
export default function FlaggedRecords({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<FlagStatus>("open");
  const [reviewing, setReviewing] = useState<RecordFlagListItem | null>(null);
  const [resolution, setResolution] = useState("");

  const { data: flags = [], isLoading } = useQuery({
    queryKey: projectFlagsKey(projectId, status),
    queryFn: () => listProjectFlags(projectId, status),
    refetchInterval: 30_000,
  });

  const resolve = useMutation({
    mutationFn: () => resolveFlag(reviewing!.id, resolution),
    onSuccess: () => {
      message.success("Flag resolved");
      setReviewing(null);
      qc.invalidateQueries({ queryKey: ["project-flags", projectId] });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't resolve the flag")),
  });

  const columns: ColumnsType<RecordFlagListItem> = [
    {
      title: "Record",
      key: "record",
      render: (_, f) => f.record_filename ?? `Record #${f.record_id}`,
    },
    { title: "Batch", dataIndex: "batch_name", render: (v: string | null) => v ?? "—" },
    { title: "Issue", dataIndex: "reason", render: (v: string) => <Typography.Paragraph style={{ margin: 0, whiteSpace: "pre-wrap" }} ellipsis={{ rows: 2, tooltip: v }}>{v}</Typography.Paragraph> },
    { title: "Flagged by", dataIndex: "raised_by_name" },
    { title: "Flagged", dataIndex: "raised_at", render: when, defaultSortOrder: "descend", sorter: (a, b) => a.raised_at.localeCompare(b.raised_at) },
    ...(status !== "open"
      ? [
          { title: status === "resolved" ? "Resolved by" : "Removed by", dataIndex: "closed_by_name" },
          ...(status === "resolved"
            ? [{ title: "Resolution", dataIndex: "resolution", render: (v: string | null) => <span style={{ whiteSpace: "pre-wrap" }}>{v}</span> }]
            : []),
          { title: status === "resolved" ? "Resolved" : "Removed", dataIndex: "closed_at", render: when },
        ] as ColumnsType<RecordFlagListItem>
      : []),
    {
      title: "",
      key: "actions",
      width: 100,
      render: (_, f) => (
        <Button
          size="small"
          type={f.status === "open" ? "primary" : "default"}
          onClick={() => {
            setReviewing(f);
            setResolution("");
          }}
        >
          {f.status === "open" ? "Review" : "View"}
        </Button>
      ),
    },
  ];

  return (
    <Card
      title="Flagged Records"
      extra={
        <Segmented
          value={status}
          onChange={(v) => setStatus(v as FlagStatus)}
          options={[
            { label: "Open", value: "open" },
            { label: "Resolved", value: "resolved" },
            { label: "Removed by indexer", value: "withdrawn" },
          ]}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={flags}
        columns={columns}
        scroll={{ x: "max-content" }}
        locale={{ emptyText: <Empty description={status === "open" ? "Nothing flagged — all clear" : "None"} /> }}
      />

      <RecordViewerDrawer recordId={reviewing?.record_id ?? null} onClose={() => setReviewing(null)}>
        {reviewing && (
          <Space direction="vertical" style={{ width: "100%", marginBottom: 16 }}>
            <Alert
              type={reviewing.status === "open" ? "warning" : "info"}
              message={`${reviewing.raised_by_name} flagged this ${when(reviewing.raised_at)}`}
              description={<span style={{ whiteSpace: "pre-wrap" }}>{reviewing.reason}</span>}
            />
            {reviewing.status === "open" ? (
              <>
                <Input.TextArea
                  rows={3}
                  maxLength={2000}
                  showCount
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  placeholder="What was decided or done — the indexer sees this on the record"
                />
                <Space>
                  <Button
                    type="primary"
                    disabled={!resolution.trim()}
                    loading={resolve.isPending}
                    onClick={() => resolve.mutate()}
                  >
                    Resolve and remove flag
                  </Button>
                  <Tag>The record stays in {reviewing.batch_name ?? "its batch"}</Tag>
                </Space>
              </>
            ) : reviewing.status === "resolved" ? (
              <Alert
                type="success"
                message={`Resolved by ${reviewing.closed_by_name} ${when(reviewing.closed_at)}`}
                description={<span style={{ whiteSpace: "pre-wrap" }}>{reviewing.resolution}</span>}
              />
            ) : (
              <Alert type="info" message={`Removed by ${reviewing.closed_by_name} ${when(reviewing.closed_at)}`} />
            )}
          </Space>
        )}
      </RecordViewerDrawer>
    </Card>
  );
}
