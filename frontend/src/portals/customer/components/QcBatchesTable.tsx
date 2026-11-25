import { useState } from "react";
import { Button, Empty, Modal, Progress, Select, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { getQcAgents, listQcBatches, lotDetailKey, qcBatchesKey, reassignQcBatch } from "@shared/api/lots";
import type { QcBatchSummary, UserRecord } from "@shared/types";
import { qcAssigneeOptions } from "./qcAssignees";

interface Props {
  projectId: number;
  /** Only this lot's batches (and no Lot column). */
  lotId?: number;
}

/** Every customer QC batch with who holds it and how far it has got, and a
 * Reassign action that moves its open records to another QC agent or
 * supervisor. */
export default function QcBatchesTable({ projectId, lotId }: Props) {
  const qc = useQueryClient();
  const [reassigning, setReassigning] = useState<QcBatchSummary | null>(null);
  const [target, setTarget] = useState<number | undefined>();

  const { data: batches = [], isLoading } = useQuery({
    queryKey: qcBatchesKey(projectId, lotId),
    queryFn: () => listQcBatches(projectId, lotId),
    refetchInterval: 15_000,
  });
  const { data: staff = [] } = useQuery({
    queryKey: ["qc-staff", projectId],
    queryFn: () => getQcAgents(projectId),
  });
  const { data: me } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  const reassign = useMutation({
    mutationFn: () => reassignQcBatch(reassigning!.id, target!),
    onSuccess: (moved: unknown[]) => {
      message.success(`${moved.length} open record${moved.length === 1 ? "" : "s"} reassigned`);
      qc.invalidateQueries({ queryKey: ["qc-batches", projectId] });
      if (reassigning?.lot_id) qc.invalidateQueries({ queryKey: lotDetailKey(reassigning.lot_id) });
      setReassigning(null);
      setTarget(undefined);
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Could not reassign the batch")),
  });

  const open = (b: QcBatchSummary) => b.pending + b.in_progress;

  const columns: ColumnType<QcBatchSummary>[] = [
    {
      title: "Batch",
      dataIndex: "name",
      width: 130,
      // "QC Batch 32 — 20261125-234709": the stamp repeats the Created column.
      render: (v: string) => (
        <Tooltip title={v}>
          <Typography.Text style={{ whiteSpace: "nowrap" }}>{v.split(" — ")[0]}</Typography.Text>
        </Tooltip>
      ),
    },
    ...(lotId === undefined
      ? [{ title: "Lot", dataIndex: "lot_name", render: (v: string | null) => v ?? "—" }]
      : []),
    {
      title: "Assigned to",
      key: "assignees",
      render: (_: unknown, b: QcBatchSummary) =>
        b.assignees.length === 0 ? (
          "—"
        ) : (
          <Space size={[4, 4]} wrap>
            {b.assignees.map((a) => (
              <Tooltip key={a.id} title={`${a.open} open of ${a.tasks}`}>
                <Tag color={a.open ? "blue" : undefined}>
                  {a.full_name}
                  {a.id === me?.id ? " (you)" : ""}
                  {a.role === "customer_supervisor" ? " · supervisor" : ""}
                </Tag>
              </Tooltip>
            ))}
          </Space>
        ),
    },
    {
      title: "Progress",
      key: "progress",
      width: 220,
      render: (_: unknown, b: QcBatchSummary) => {
        const done = b.completed + b.failed;
        return (
          <Tooltip
            title={`${b.completed} passed · ${b.failed} rejected · ${b.in_progress} in progress · ${b.pending} not started`}
          >
            <Progress
              percent={b.total ? Math.round((done / b.total) * 100) : 0}
              size="small"
              format={() => `${done}/${b.total}`}
            />
          </Tooltip>
        );
      },
    },
    {
      title: "Created",
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: "",
      key: "action",
      width: 110,
      render: (_: unknown, b: QcBatchSummary) => (
        <Tooltip title={open(b) ? undefined : "Every record in this batch has been reviewed"}>
          <Button size="small" disabled={!open(b)} onClick={() => setReassigning(b)}>
            Reassign
          </Button>
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={batches}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="No QC batches assigned yet" /> }}
      />
      <Modal
        title={reassigning ? `Reassign ${reassigning.name}` : "Reassign"}
        open={!!reassigning}
        onCancel={() => {
          setReassigning(null);
          setTarget(undefined);
        }}
        okText="Reassign"
        okButtonProps={{ disabled: !target, loading: reassign.isPending }}
        onOk={() => reassign.mutate()}
        destroyOnClose
      >
        {reassigning && (
          <>
            <Typography.Paragraph>
              The {open(reassigning)} open record{open(reassigning) === 1 ? "" : "s"} in this batch move to the person
              you pick. Records already reviewed stay with whoever reviewed them. A record someone has open goes back to
              the start for the new person, so anything not yet submitted on it is lost.
            </Typography.Paragraph>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="Choose a QC agent or supervisor"
              style={{ width: "100%" }}
              value={target}
              onChange={setTarget}
              options={qcAssigneeOptions(staff, me?.id)}
            />
          </>
        )}
      </Modal>
    </>
  );
}
