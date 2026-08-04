import { useState } from "react";
import { Button, Input, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import RecordViewerDrawer from "@shared/components/RecordViewerDrawer";
import type { DocRecord, RecordListResponse } from "@shared/types";

const RECORD_STATUS_OPTIONS = [
  "pending", "indexing", "indexed", "qa_pending", "qa_passed", "qa_failed",
  "qc_pending", "qc_passed", "qc_failed", "withdrawn", "ineligible",
  "excluded", "lapsed", "illegible",
];

const PAGE_SIZE = 50;

function RequeueConfirmModal({
  target, count, onClose, onConfirm, loading,
}: {
  target: "indexing" | "qa";
  count: number;
  onClose: () => void;
  onConfirm: (note: string) => void;
  loading: boolean;
}) {
  const [note, setNote] = useState("");
  const label = target === "indexing" ? "re-indexing" : "a fresh QA pass";

  return (
    <Modal
      title={`Send ${count} record${count === 1 ? "" : "s"} back for ${label}?`}
      open
      onCancel={onClose}
      onOk={() => onConfirm(note)}
      okButtonProps={{ loading }}
      okText="Requeue"
    >
      <Typography.Paragraph type="secondary">
        Any active lock on these records will be released, and any in-progress task on
        them will be closed out. This action is logged.
      </Typography.Paragraph>
      <Input.TextArea
        placeholder="Optional note (why this needs rework)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
      />
    </Modal>
  );
}

export default function RecordsManagementReviewTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [viewerRecordId, setViewerRecordId] = useState<number | null>(null);
  const [requeueTarget, setRequeueTarget] = useState<"indexing" | "qa" | null>(null);

  const { data, isLoading } = useQuery<RecordListResponse>({
    queryKey: ["project-records", projectId, status, page],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/records`, {
          params: { status, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
          paramsSerializer: { indexes: null },
        })
        .then((r) => r.data),
  });

  const invalidateList = () => qc.invalidateQueries({ queryKey: ["project-records", projectId] });

  const requeueMutation = useMutation({
    mutationFn: ({ target, note }: { target: "indexing" | "qa"; note: string }) =>
      api.post("/records/requeue", {
        record_ids: selectedIds,
        target,
        note: note || undefined,
      }),
    onSuccess: (_data, { target }) => {
      message.success(`Sent ${selectedIds.length} record(s) back for ${target === "indexing" ? "re-indexing" : "QA"}`);
      setSelectedIds([]);
      setRequeueTarget(null);
      invalidateList();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Failed to requeue records")),
  });

  const exportMutation = useMutation({
    mutationFn: () =>
      api.post("/records/export", { record_ids: selectedIds }, { responseType: "blob" }),
    onSuccess: (res) => {
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `records_export_${Date.now()}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Export failed")),
  });

  const columns: ColumnsType<DocRecord> = [
    { title: "ID", dataIndex: "id", width: 70 },
    {
      title: "Filename",
      dataIndex: "original_filename",
      render: (v: string | null, r) => v ?? r.source_identifier ?? `Record #${r.id}`,
    },
    { title: "Status", dataIndex: "status", render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag> },
    { title: "Indexed By", dataIndex: "indexed_by_name", render: (v: string | null) => v ?? "—" },
    { title: "QA'd By", dataIndex: "qa_by_name", render: (v: string | null) => v ?? "—" },
    {
      title: "",
      key: "actions",
      render: (_: unknown, record: DocRecord) => (
        <Button size="small" onClick={() => setViewerRecordId(record.id)}>
          View
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          mode="multiple"
          placeholder="Filter by status"
          allowClear
          style={{ minWidth: 260 }}
          options={RECORD_STATUS_OPTIONS.map((s) => ({ label: s.replace(/_/g, " "), value: s }))}
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
        />
        <Button
          disabled={selectedIds.length === 0}
          onClick={() => setRequeueTarget("indexing")}
        >
          Requeue → Indexing
        </Button>
        <Button
          disabled={selectedIds.length === 0}
          onClick={() => setRequeueTarget("qa")}
        >
          Requeue → QA
        </Button>
        <Button
          disabled={selectedIds.length === 0}
          loading={exportMutation.isPending}
          onClick={() => exportMutation.mutate()}
        >
          Export ZIP
        </Button>
      </Space>

      {selectedIds.length > 0 && (
        <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
          {selectedIds.length} record{selectedIds.length === 1 ? "" : "s"} selected
        </Typography.Text>
      )}

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys) => setSelectedIds(keys as number[]),
        }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          onChange: setPage,
          showSizeChanger: false,
        }}
      />

      <RecordViewerDrawer recordId={viewerRecordId} onClose={() => setViewerRecordId(null)} />

      {requeueTarget && (
        <RequeueConfirmModal
          target={requeueTarget}
          count={selectedIds.length}
          loading={requeueMutation.isPending}
          onClose={() => setRequeueTarget(null)}
          onConfirm={(note) => requeueMutation.mutate({ target: requeueTarget, note })}
        />
      )}
    </div>
  );
}
