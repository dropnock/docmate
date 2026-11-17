import { useState, type Key } from "react";
import { Button, Card, Empty, Input, Modal, Segmented, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { formatApiError } from "@shared/api/errors";
import { holdRecords, listHolds, projectHoldsKey, releaseHolds, type RecordHoldListItem } from "@shared/api/holds";
import type { CabinetRecord } from "@shared/types";

const when = (iso: string | null) => (iso ? dayjs(iso).format("D MMM YYYY, HH:mm") : "—");
const recordName = (r: { original_filename: string | null; source_identifier: string | null; id: number }) =>
  r.original_filename ?? r.source_identifier ?? `Record #${r.id}`;

interface Props {
  projectId: number;
  cabinetId: number;
  /** Pending, unbatched records that aren't on hold — what allocation draws from. */
  available: CabinetRecord[];
  loading: boolean;
}

/** Cabinet Assignment's list of unassigned records. A supervisor holds
 * records back so allocation skips them, sees what's on hold (and why), and
 * cancels holds. The backend refuses to batch a held record either way. */
export default function UnassignedRecordsCard({ projectId, cabinetId, available, loading }: Props) {
  const qc = useQueryClient();
  const [view, setView] = useState<"available" | "held">("available");
  const [selected, setSelected] = useState<number[]>([]);
  const [dialog, setDialog] = useState<"hold" | "release" | null>(null);
  const [text, setText] = useState("");

  const { data: holds = [], isLoading: holdsLoading } = useQuery({
    queryKey: projectHoldsKey(projectId),
    queryFn: () => listHolds(projectId),
  });

  const done = (msg: string) => {
    message.success(msg);
    setSelected([]);
    setDialog(null);
    setText("");
    qc.invalidateQueries({ queryKey: projectHoldsKey(projectId) });
    qc.invalidateQueries({ queryKey: ["cabinet-records", cabinetId] });
  };

  const hold = useMutation({
    mutationFn: () => holdRecords(projectId, selected, text),
    onSuccess: (r) => done(`${r.length} record${r.length === 1 ? "" : "s"} put on hold`),
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't put the records on hold")),
  });
  const release = useMutation({
    mutationFn: () => releaseHolds(projectId, selected, text),
    onSuccess: (r) => done(`Hold cancelled on ${r.length} record${r.length === 1 ? "" : "s"}`),
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't cancel the hold")),
  });

  const availableColumns: ColumnsType<CabinetRecord> = [
    { title: "Record", key: "record", render: (_, r) => recordName(r) },
    { title: "Identifier", dataIndex: "source_identifier", render: (v: string | null) => v ?? "—" },
  ];
  const heldColumns: ColumnsType<RecordHoldListItem> = [
    { title: "Record", key: "record", render: (_, h) => recordName({ original_filename: h.record_filename, source_identifier: h.source_identifier, id: h.record_id }) },
    {
      title: "Reason",
      dataIndex: "reason",
      render: (v: string) => (
        <Typography.Paragraph style={{ margin: 0, whiteSpace: "pre-wrap" }} ellipsis={{ rows: 2, tooltip: v }}>
          {v}
        </Typography.Paragraph>
      ),
    },
    { title: "Held by", dataIndex: "held_by_name" },
    { title: "Held", dataIndex: "held_at", render: when },
  ];

  const switchView = (v: "available" | "held") => {
    setView(v);
    setSelected([]);
  };
  const rowSelection = {
    selectedRowKeys: selected,
    onChange: (keys: Key[]) => setSelected(keys as number[]),
  };

  return (
    <Card
      title="Unassigned records"
      style={{ marginBottom: 16 }}
      extra={
        <Space wrap>
          <Segmented
            value={view}
            onChange={(v) => switchView(v as "available" | "held")}
            options={[
              { label: `Available (${available.length})`, value: "available" },
              { label: `On hold (${holds.length})`, value: "held" },
            ]}
          />
          {view === "available" ? (
            <Button disabled={!selected.length} onClick={() => { setText(""); setDialog("hold"); }}>
              Hold selected{selected.length ? ` (${selected.length})` : ""}
            </Button>
          ) : (
            <Button disabled={!selected.length} onClick={() => { setText(""); setDialog("release"); }}>
              Cancel hold{selected.length ? ` (${selected.length})` : ""}
            </Button>
          )}
        </Space>
      }
    >
      {view === "available" ? (
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={available}
          columns={availableColumns}
          rowSelection={rowSelection}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }}
          locale={{ emptyText: <Empty description="No unassigned records" /> }}
        />
      ) : (
        <Table
          rowKey="record_id"
          size="small"
          loading={holdsLoading}
          dataSource={holds}
          columns={heldColumns}
          rowSelection={rowSelection}
          scroll={{ x: "max-content" }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true }}
          locale={{ emptyText: <Empty description="Nothing on hold" /> }}
        />
      )}

      <Modal
        open={dialog === "hold"}
        title={`Hold ${selected.length} record${selected.length === 1 ? "" : "s"}`}
        okText="Hold"
        okButtonProps={{ disabled: !text.trim(), loading: hold.isPending }}
        onOk={() => hold.mutate()}
        onCancel={() => setDialog(null)}
      >
        <Typography.Paragraph type="secondary">
          Held records are left out when batches are allocated, until the hold is cancelled.
        </Typography.Paragraph>
        <Input.TextArea
          rows={3}
          maxLength={2000}
          showCount
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Why these records are on hold"
        />
      </Modal>
      <Modal
        open={dialog === "release"}
        title={`Cancel the hold on ${selected.length} record${selected.length === 1 ? "" : "s"}`}
        okText="Cancel hold"
        cancelText="Keep on hold"
        okButtonProps={{ loading: release.isPending }}
        onOk={() => release.mutate()}
        onCancel={() => setDialog(null)}
      >
        <Typography.Paragraph type="secondary">They become available for allocation again.</Typography.Paragraph>
        <Input.TextArea
          rows={2}
          maxLength={2000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Note (optional)"
        />
      </Modal>
    </Card>
  );
}
