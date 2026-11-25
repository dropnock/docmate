import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Drawer, Empty, InputNumber, Row, Select, Spin, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { createQcBatches, getLot, getQcAgents, lotDetailKey } from "@shared/api/lots";
import { useDocumentTypes } from "@shared/hooks/useDocumentTypes";
import type { LotDetail, UserRecord } from "@shared/types";
import QcBatchesTable from "./QcBatchesTable";
import { qcAssigneeName, qcAssigneeOptions } from "./qcAssignees";

interface Props {
  lotId: number | undefined;
  projectId: number;
  onClose: () => void;
}

type SampledRecord = LotDetail["records"][number];

const OPEN = new Set(["pending", "in_progress"]);
const QC_TASK_LABEL: Record<string, string> = {
  pending: "Not started",
  in_progress: "In progress",
  completed: "Passed",
  failed: "Rejected",
};

/** Still to be handed out: sampled, waiting for QC, and not in an open QC
 * batch (the same rule the server applies). */
const isUnassigned = (r: SampledRecord) =>
  r.is_sampled && r.status === "qc_pending" && !(r.qc_task && OPEN.has(r.qc_task.status));

export default function LotAssignQcDrawer({ lotId, projectId, onClose }: Props) {
  const qc = useQueryClient();
  const [selectedAgents, setSelectedAgents] = useState<number[]>([]);
  const [batchSize, setBatchSize] = useState<number>(25);
  const [batchCount, setBatchCount] = useState<number>(1);
  const [selectedDocType, setSelectedDocType] = useState<number | undefined>();

  const { data: lotDetail } = useQuery({
    queryKey: lotDetailKey(lotId),
    queryFn: () => getLot(lotId as number),
    enabled: !!lotId,
    refetchInterval: 10_000,
  });
  const { data: staff = [] } = useQuery({
    queryKey: ["qc-staff", projectId],
    queryFn: () => getQcAgents(projectId),
    enabled: !!lotId,
  });
  const { data: me } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });
  const { data: docTypes = [] } = useDocumentTypes(projectId);

  // A project with one document type needs no choice.
  useEffect(() => {
    if (!selectedDocType && docTypes.length === 1) setSelectedDocType(docTypes[0].id);
  }, [docTypes, selectedDocType]);

  const sampledRecords: SampledRecord[] = useMemo(
    () => lotDetail?.records.filter((r) => r.is_sampled) ?? [],
    [lotDetail],
  );
  const unassigned = useMemo(() => sampledRecords.filter(isUnassigned), [sampledRecords]);
  const maxBatches = Math.max(1, Math.ceil(unassigned.length / Math.max(1, batchSize)));

  // Keep the batch count within what the unassigned records can fill.
  useEffect(() => {
    if (batchCount > maxBatches) setBatchCount(maxBatches);
  }, [batchCount, maxBatches]);

  // Exactly the batches the supervisor asked for, filled in record order and
  // shared round-robin between the people picked; the rest stay unassigned.
  const batchPreview = useMemo(() => {
    if (!selectedAgents.length || batchSize < 1) return [];
    const ids = unassigned.map((r) => r.record_id);
    const chunks: { agentId: number; recordIds: number[] }[] = [];
    for (let i = 0; i < batchCount && i * batchSize < ids.length; i++) {
      chunks.push({
        agentId: selectedAgents[i % selectedAgents.length],
        recordIds: ids.slice(i * batchSize, (i + 1) * batchSize),
      });
    }
    return chunks;
  }, [unassigned, selectedAgents, batchSize, batchCount]);
  const assigning = batchPreview.reduce((n, b) => n + b.recordIds.length, 0);

  const createQcBatchesMutation = useMutation({
    mutationFn: () => {
      if (!selectedDocType) throw new Error("Select a document type");
      if (!selectedAgents.length) throw new Error("Choose who gets the batches");
      if (!batchPreview.length) throw new Error("No unassigned sampled records to assign");
      return createQcBatches(lotId as number, {
        project_id: projectId,
        document_type_id: selectedDocType,
        assignments: batchPreview.map((b) => ({ agent_id: b.agentId, record_ids: b.recordIds })),
      });
    },
    onSuccess: () => {
      message.success(`${batchPreview.length} QC batch${batchPreview.length === 1 ? "" : "es"} assigned`);
      qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
      qc.invalidateQueries({ queryKey: ["qc-batches", projectId] });
      setSelectedAgents([]);
      setBatchCount(1);
    },
    onError: (e: unknown) => message.error(formatApiError(e, String((e as Error).message))),
  });

  const columns: ColumnType<SampledRecord>[] = [
    { title: "Record ID", dataIndex: "record_id", width: 90 },
    { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
    {
      title: "Assigned to",
      key: "assigned_to",
      render: (_: unknown, r: SampledRecord) =>
        r.qc_task?.assigned_to_name ? (
          `${r.qc_task.assigned_to_name}${r.qc_task.assigned_to === me?.id ? " (you)" : ""}`
        ) : (
          <Typography.Text type="secondary">Unassigned</Typography.Text>
        ),
    },
    {
      title: "QC",
      key: "qc",
      render: (_: unknown, r: SampledRecord) =>
        r.qc_task ? <Tag>{QC_TASK_LABEL[r.qc_task.status] ?? r.qc_task.status}</Tag> : "—",
    },
  ];

  return (
    <Drawer
      title={lotDetail ? `Assign to QC: ${lotDetail.name}` : "Assign to QC"}
      open={!!lotId}
      onClose={onClose}
      width={760}
    >
      {!lotDetail ? (
        <Spin />
      ) : lotDetail.status === "released" ? (
        <Empty description="Apply a sample from Settings before assigning records to QC." />
      ) : (
        <>
          {lotDetail.status === "qc_in_progress" && (
            <Card
              title={`Assign batches — ${unassigned.length} of ${sampledRecords.length} sampled records unassigned`}
              style={{ marginBottom: 16 }}
            >
              {unassigned.length === 0 ? (
                <Alert type="success" showIcon message="Every sampled record has been assigned." />
              ) : (
                <>
                  <Row gutter={16} align="bottom" style={{ marginBottom: 16 }}>
                    <Col>
                      <Typography.Text>Number of batches</Typography.Text>
                      <br />
                      <InputNumber
                        min={1}
                        max={maxBatches}
                        value={batchCount}
                        onChange={(v) => setBatchCount(v ?? 1)}
                        style={{ width: 110, marginTop: 4 }}
                      />
                    </Col>
                    <Col>
                      <Typography.Text>Records per batch</Typography.Text>
                      <br />
                      <InputNumber
                        min={1}
                        max={unassigned.length}
                        value={batchSize}
                        onChange={(v) => setBatchSize(v ?? 25)}
                        style={{ width: 110, marginTop: 4 }}
                      />
                    </Col>
                    <Col flex="auto">
                      <Typography.Text>Document type</Typography.Text>
                      <br />
                      <Select
                        placeholder="Select type"
                        style={{ width: "100%", maxWidth: 260, marginTop: 4 }}
                        options={docTypes.map((d) => ({ label: d.name, value: d.id }))}
                        onChange={setSelectedDocType}
                        value={selectedDocType}
                      />
                    </Col>
                  </Row>

                  <Typography.Text strong>Assign to</Typography.Text>
                  <Select
                    mode="multiple"
                    showSearch
                    optionFilterProp="label"
                    placeholder="QC agents or supervisors (including yourself)"
                    style={{ width: "100%", marginTop: 8, marginBottom: 16 }}
                    value={selectedAgents}
                    onChange={setSelectedAgents}
                    options={qcAssigneeOptions(staff, me?.id)}
                  />

                  {batchPreview.length > 0 && (
                    <>
                      <Typography.Text type="secondary">
                        {batchPreview.length} batch{batchPreview.length !== 1 ? "es" : ""}, {assigning} record
                        {assigning !== 1 ? "s" : ""}
                        {unassigned.length - assigning > 0
                          ? ` — ${unassigned.length - assigning} stay unassigned for later`
                          : " — every unassigned record"}
                        :
                      </Typography.Text>
                      <Row gutter={[8, 8]} style={{ marginTop: 8, marginBottom: 16 }}>
                        {selectedAgents.map((agentId) => {
                          const agentBatches = batchPreview.filter((b) => b.agentId === agentId);
                          if (!agentBatches.length) return null;
                          const total = agentBatches.reduce((n, b) => n + b.recordIds.length, 0);
                          return (
                            <Col key={agentId}>
                              <Tag>
                                {qcAssigneeName(staff, agentId, me?.id)} — {agentBatches.length} batch
                                {agentBatches.length !== 1 ? "es" : ""}, {total} records
                              </Tag>
                            </Col>
                          );
                        })}
                      </Row>
                    </>
                  )}

                  <Button
                    type="primary"
                    loading={createQcBatchesMutation.isPending}
                    disabled={!selectedDocType || !batchPreview.length}
                    onClick={() => createQcBatchesMutation.mutate()}
                  >
                    Assign {batchPreview.length || ""} batch{batchPreview.length === 1 ? "" : "es"}
                  </Button>
                </>
              )}
            </Card>
          )}

          <Card title="QC batches for this lot" style={{ marginBottom: 16 }}>
            <QcBatchesTable projectId={projectId} lotId={lotDetail.id} />
          </Card>

          <Card title="Sampled records">
            <Table rowKey="record_id" size="small" dataSource={sampledRecords} pagination={{ pageSize: 20 }} columns={columns} />
          </Card>
        </>
      )}
    </Drawer>
  );
}
