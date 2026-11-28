import { useMemo, useState } from "react";
import { Button, Empty, Modal, Popconfirm, Progress, Select, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { getQcAgents, listQcBatches, lotDetailKey, qcBatchesKey, reassignQcBatch } from "@shared/api/lots";
import { submitQcBatch } from "@shared/api/tasks";
import type { QcBatchSummary, UserRecord } from "@shared/types";
import { qcAssigneeOptions } from "./qcAssignees";

type Stage = "not_started" | "in_progress" | "to_submit" | "submitted";
const STAGE_LABEL: Record<Stage, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  to_submit: "Reviewed, not submitted",
  submitted: "Submitted",
};
/** Submitted: the reviewer has handed it in. Reviewed, not submitted: nothing
 * left open, but the reviewer can still go back to its records. Not
 * started: nobody has opened a record yet. */
const stage = (b: QcBatchSummary): Stage =>
  b.submitted
    ? "submitted"
    : b.pending + b.in_progress === 0
      ? "to_submit"
      : b.pending === b.total
        ? "not_started"
        : "in_progress";

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

  const submit = useMutation({
    mutationFn: (b: QcBatchSummary) => submitQcBatch(b.id),
    onSuccess: (_: unknown, b: QcBatchSummary) => {
      message.success(`${b.name.split(" — ")[0]} submitted`);
      qc.invalidateQueries({ queryKey: ["qc-batches", projectId] });
      if (b.lot_id) qc.invalidateQueries({ queryKey: lotDetailKey(b.lot_id) });
      qc.invalidateQueries({ queryKey: ["customer-lots", projectId] });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Could not submit the batch")),
  });

  const open = (b: QcBatchSummary) => b.pending + b.in_progress;

  // Everyone holding a batch in the list, for the Assigned to filter.
  const assigneeFilters = useMemo(() => {
    const seen = new Map<number, string>();
    for (const b of batches) for (const a of b.assignees) seen.set(a.id, a.full_name);
    return [...seen.entries()]
      .sort((x, y) => x[1].localeCompare(y[1]))
      .map(([id, name]) => ({ text: `${name}${id === me?.id ? " (you)" : ""}`, value: id }));
  }, [batches, me?.id]);

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
      filters: assigneeFilters,
      filterSearch: true,
      onFilter: (value, b) => b.assignees.some((a) => a.id === value),
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
      filters: (Object.keys(STAGE_LABEL) as Stage[]).map((k) => ({ text: STAGE_LABEL[k], value: k })),
      onFilter: (value, b) => stage(b) === value,
      render: (_: unknown, b: QcBatchSummary) => {
        const done = b.completed + b.failed;
        const st = stage(b);
        return (
          <Tooltip
            title={`${b.completed} passed · ${b.failed} rejected · ${b.in_progress} in progress · ${b.pending} not started`}
          >
            <Progress
              percent={b.total ? Math.round((done / b.total) * 100) : 0}
              size="small"
              status={st === "submitted" ? "success" : "normal"}
              format={() => `${done}/${b.total}`}
            />
            {(st === "to_submit" || st === "submitted") && (
              <Tag color={st === "submitted" ? "success" : "processing"} style={{ marginTop: 2 }}>
                {STAGE_LABEL[st]}
              </Tag>
            )}
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
      render: (_: unknown, b: QcBatchSummary) =>
        stage(b) === "to_submit" ? (
          <Popconfirm
            title="Submit this batch for the reviewer?"
            description="Its records can't be reviewed again after this."
            okText="Submit"
            onConfirm={() => submit.mutate(b)}
          >
            <Tooltip title="Every record is reviewed; the reviewer hasn't submitted the batch yet">
              <Button size="small" loading={submit.isPending && submit.variables?.id === b.id}>
                Submit
              </Button>
            </Tooltip>
          </Popconfirm>
        ) : (
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
        pagination={{ defaultPageSize: 20, hideOnSinglePage: true }}
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
