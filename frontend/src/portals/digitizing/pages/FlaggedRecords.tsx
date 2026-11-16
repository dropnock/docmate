import { useState, type Key } from "react";
import {
  Alert, Button, Card, Divider, Empty, Input, Popconfirm, Segmented, Space, Table, Tag, Tooltip, Typography, message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { Download, Share2 } from "lucide-react";
import { formatApiError } from "@shared/api/errors";
import {
  exportProjectFlags, listProjectFlags, projectFlagsKey, resolveFlag, shareFlags, unshareFlag,
  type FlagStatus, type RecordFlagListItem,
} from "@shared/api/flags";
import FlagDiscussion from "@shared/components/FlagDiscussion";
import RecordViewerDrawer from "@shared/components/RecordViewerDrawer";
import { useFlagReview } from "@shared/hooks/useFlagReview";

const when = (iso: string | null) => (iso ? dayjs(iso).format("D MMM YYYY, HH:mm") : "—");

/** Supervisor's queue of records indexers have flagged. Review opens the
 * record (image + data) with the indexer's note; resolving records what was
 * done and removes the flag. The record stays in its batch either way.
 * Open flags can be shared with the customer's supervisors, who can then
 * see the record and discuss it with the team. */
export default function FlaggedRecords({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<FlagStatus>("open");
  const [resolution, setResolution] = useState("");
  const [selected, setSelected] = useState<number[]>([]);

  const { data: flags = [], isLoading } = useQuery({
    queryKey: projectFlagsKey(projectId, status),
    queryFn: () => listProjectFlags(projectId, status),
    refetchInterval: 30_000,
  });
  const { reviewing, open, close } = useFlagReview(flags);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["project-flags", projectId] });
    qc.invalidateQueries({ queryKey: ["flag"] });
  };

  const resolve = useMutation({
    mutationFn: () => resolveFlag(reviewing!.id, resolution),
    onSuccess: () => {
      message.success("Flag resolved");
      close();
      refresh();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't resolve the flag")),
  });

  const share = useMutation({
    mutationFn: (ids: number[]) => shareFlags(projectId, ids),
    onSuccess: (r) => {
      message.success(`${r.length} flag${r.length === 1 ? "" : "s"} shared with the customer`);
      setSelected([]);
      refresh();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't share with the customer")),
  });

  const unshare = useMutation({
    mutationFn: (id: number) => unshareFlag(id),
    onSuccess: () => {
      message.success("No longer shared with the customer");
      refresh();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't stop sharing")),
  });

  const exportCsv = useMutation({
    mutationFn: () => exportProjectFlags(projectId, status),
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't export the flags")),
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
    {
      title: "Customer",
      key: "shared",
      render: (_, f) =>
        f.shared_at ? (
          <Tooltip title={`Shared by ${f.shared_by_name ?? "—"} ${when(f.shared_at)}`}>
            <Tag color="gold">Shared{f.comment_count ? ` · ${f.comment_count} message${f.comment_count === 1 ? "" : "s"}` : ""}</Tag>
          </Tooltip>
        ) : null,
    },
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
            open(f);
            setResolution("");
          }}
        >
          {f.status === "open" ? "Review" : "View"}
        </Button>
      ),
    },
  ];

  const selectedUnshared = selected.filter((id) => !flags.find((f) => f.id === id)?.shared_at);

  return (
    <Card
      title="Flagged Records"
      extra={
        <Space wrap>
          <Segmented
            value={status}
            onChange={(v) => {
              setStatus(v as FlagStatus);
              setSelected([]);
            }}
            options={[
              { label: "Open", value: "open" },
              { label: "Resolved", value: "resolved" },
              { label: "Removed by indexer", value: "withdrawn" },
            ]}
          />
          {status === "open" && (
            <Popconfirm
              title={`Share ${selectedUnshared.length} flagged record${selectedUnshared.length === 1 ? "" : "s"} with the customer?`}
              description="The customer's supervisors can then see the record and the issue, and discuss it with you."
              okText="Share"
              disabled={!selectedUnshared.length}
              onConfirm={() => share.mutate(selectedUnshared)}
            >
              <Button icon={<Share2 size={14} />} disabled={!selectedUnshared.length} loading={share.isPending}>
                Share with customer{selectedUnshared.length ? ` (${selectedUnshared.length})` : ""}
              </Button>
            </Popconfirm>
          )}
          <Button
            icon={<Download size={14} />}
            disabled={!flags.length}
            loading={exportCsv.isPending}
            onClick={() => exportCsv.mutate()}
          >
            Export CSV
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={flags}
        columns={columns}
        scroll={{ x: "max-content" }}
        rowSelection={
          status === "open"
            ? { selectedRowKeys: selected, onChange: (keys: Key[]) => setSelected(keys as number[]) }
            : undefined
        }
        locale={{ emptyText: <Empty description={status === "open" ? "Nothing flagged — all clear" : "None"} /> }}
      />

      <RecordViewerDrawer recordId={reviewing?.record_id ?? null} onClose={close}>
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
                <Space wrap>
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

            <Divider style={{ margin: "8px 0" }} />
            {reviewing.shared_at ? (
              <>
                <Space wrap>
                  <Tag color="gold">Shared with the customer by {reviewing.shared_by_name} {when(reviewing.shared_at)}</Tag>
                  {reviewing.status === "open" && (
                    <Popconfirm
                      title="Stop sharing with the customer?"
                      description="They won't see this flag or its discussion any more."
                      okText="Stop sharing"
                      onConfirm={() => unshare.mutate(reviewing.id)}
                    >
                      <Button size="small" loading={unshare.isPending}>Stop sharing</Button>
                    </Popconfirm>
                  )}
                </Space>
                <FlagDiscussion flag={reviewing} side="digitizing" />
              </>
            ) : reviewing.status === "open" ? (
              <Space wrap>
                <Typography.Text type="secondary">Need the customer's guidance on this record?</Typography.Text>
                <Popconfirm
                  title="Share with the customer?"
                  description="Their supervisors can then see the record and the issue, and discuss it with you."
                  okText="Share"
                  onConfirm={() => share.mutate([reviewing.id])}
                >
                  <Button size="small" icon={<Share2 size={14} />} loading={share.isPending}>
                    Share with customer
                  </Button>
                </Popconfirm>
              </Space>
            ) : null}
          </Space>
        )}
      </RecordViewerDrawer>
    </Card>
  );
}
