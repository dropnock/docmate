import { useEffect, useState } from "react";
import { Alert, Button, Card, Divider, Empty, Input, Segmented, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { listProjectFlags, projectFlagsKey, type FlagStatus, type RecordFlagListItem } from "@shared/api/flags";
import FlagDiscussion from "@shared/components/FlagDiscussion";
import RecordViewerDrawer from "@shared/components/RecordViewerDrawer";
import { useFlagReview } from "@shared/hooks/useFlagReview";

const when = (iso: string | null) => (iso ? dayjs(iso).format("D MMM YYYY, HH:mm") : "—");

/** Flagged records the digitization team has shared with the customer for
 * guidance. The customer supervisor opens the record (image + data) and the
 * issue, and replies in the discussion; the team resolves the flag. */
export default function CustomerFlaggedRecords({ projectId }: { projectId: number }) {
  const [status, setStatus] = useState<FlagStatus>("open");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  // Search as you type, once typing pauses.
  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const { data: flags = [], isLoading, isFetching } = useQuery({
    queryKey: projectFlagsKey(projectId, status, q),
    queryFn: () => listProjectFlags(projectId, status, q),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });
  const { reviewing, open, close } = useFlagReview(flags);

  const columns: ColumnsType<RecordFlagListItem> = [
    { title: "Record", key: "record", render: (_, f) => f.record_filename ?? f.source_identifier ?? `Record #${f.record_id}` },
    {
      title: "Issue",
      dataIndex: "reason",
      render: (v: string) => (
        <Typography.Paragraph style={{ margin: 0, whiteSpace: "pre-wrap" }} ellipsis={{ rows: 2, tooltip: v }}>
          {v}
        </Typography.Paragraph>
      ),
    },
    { title: "Shared by", dataIndex: "shared_by_name" },
    { title: "Shared", dataIndex: "shared_at", render: when, defaultSortOrder: "descend", sorter: (a, b) => (a.shared_at ?? "").localeCompare(b.shared_at ?? "") },
    { title: "Messages", dataIndex: "comment_count", render: (n: number) => (n ? <Tag>{n}</Tag> : "—") },
    ...(status === "resolved"
      ? ([
          { title: "Resolution", dataIndex: "resolution", render: (v: string | null) => <span style={{ whiteSpace: "pre-wrap" }}>{v}</span> },
          { title: "Resolved", dataIndex: "closed_at", render: when },
        ] as ColumnsType<RecordFlagListItem>)
      : []),
    {
      title: "",
      key: "actions",
      width: 100,
      render: (_, f) => (
        <Button size="small" type={f.status === "open" ? "primary" : "default"} onClick={() => open(f)}>
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
            { label: "Withdrawn", value: "withdrawn" },
          ]}
        />
      }
    >
      <Typography.Paragraph type="secondary">
        Records the digitization team has asked for your guidance on. Open one to see the record and the issue, and
        reply in the discussion.
      </Typography.Paragraph>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input.Search
          allowClear
          placeholder="Search record, issue or discussion"
          style={{ width: 360 }}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(v) => setQ(v.trim())}
          loading={isFetching && !!q}
          data-flag-search
        />
        {q && !isFetching && (
          <Typography.Text type="secondary" data-flag-search-count>
            {flags.length} {flags.length === 1 ? "match" : "matches"} in {status}
          </Typography.Text>
        )}
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={flags}
        columns={columns}
        scroll={{ x: "max-content" }}
        locale={{
          emptyText: (
            <Empty
              description={
                q
                  ? `No ${status} flags match "${q}". Try the other tabs or a different search.`
                  : status === "open"
                    ? "Nothing needs your guidance"
                    : "None"
              }
            />
          ),
        }}
      />

      <RecordViewerDrawer recordId={reviewing?.record_id ?? null} onClose={close}>
        {reviewing && (
          <Space direction="vertical" style={{ width: "100%", marginBottom: 16 }}>
            <Alert
              type={reviewing.status === "open" ? "warning" : "info"}
              message={`Flagged by the digitization team ${when(reviewing.raised_at)}`}
              description={<span style={{ whiteSpace: "pre-wrap" }}>{reviewing.reason}</span>}
            />
            {reviewing.status === "resolved" && (
              <Alert
                type="success"
                message={`Resolved by ${reviewing.closed_by_name} ${when(reviewing.closed_at)}`}
                description={<span style={{ whiteSpace: "pre-wrap" }}>{reviewing.resolution}</span>}
              />
            )}
            {reviewing.status === "withdrawn" && <Alert type="info" message={`Withdrawn ${when(reviewing.closed_at)}`} />}
            <Divider style={{ margin: "8px 0" }} />
            <FlagDiscussion flag={reviewing} side="customer" />
          </Space>
        )}
      </RecordViewerDrawer>
    </Card>
  );
}
