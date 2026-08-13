import { useMemo, useState } from "react";
import { Button, Card, Col, Drawer, Empty, InputNumber, Row, Select, Spin, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import { formatApiError } from "@shared/api/errors";
import { createQcBatches, getLot, getQcAgents, lotDetailKey } from "@shared/api/lots";
import StatusDot from "@shared/components/StatusDot";
import { useDocumentTypes } from "@shared/hooks/useDocumentTypes";
import type { LotDetail } from "@shared/types";

interface Props {
  lotId: number | undefined;
  projectId: number;
  onClose: () => void;
}

type SampledRecord = LotDetail["records"][number];

export default function LotAssignQcDrawer({ lotId, projectId, onClose }: Props) {
  const qc = useQueryClient();
  const [selectedAgents, setSelectedAgents] = useState<number[]>([]);
  const [batchSize, setBatchSize] = useState<number>(25);
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

  const { data: docTypes = [] } = useDocumentTypes(projectId);

  const sampledRecords: SampledRecord[] = lotDetail?.records.filter((r) => r.is_sampled) ?? [];

  // Split sampled records into chunks of batchSize, assign round-robin to selected agents
  const batchPreview = useMemo(() => {
    if (!selectedAgents.length || batchSize < 1) return [];
    const recordIds = sampledRecords.map((r) => r.record_id);
    const chunks: { agentId: number; recordIds: number[] }[] = [];
    for (let i = 0; i < recordIds.length; i += batchSize) {
      const agentId = selectedAgents[chunks.length % selectedAgents.length];
      chunks.push({ agentId, recordIds: recordIds.slice(i, i + batchSize) });
    }
    return chunks;
  }, [sampledRecords, selectedAgents, batchSize]);

  const createQcBatchesMutation = useMutation({
    mutationFn: () => {
      if (!selectedDocType) throw new Error("Select a document type");
      if (!selectedAgents.length) throw new Error("Select at least one QC agent");
      if (!batchPreview.length) throw new Error("No sampled records to assign");
      return createQcBatches(lotId as number, {
        project_id: projectId,
        document_type_id: selectedDocType,
        assignments: batchPreview.map((b) => ({ agent_id: b.agentId, record_ids: b.recordIds })),
      });
    },
    onSuccess: () => {
      message.success("QC batches created and assigned");
      qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
      setSelectedAgents([]);
    },
    onError: (e: unknown) => message.error(formatApiError(e, String((e as Error).message))),
  });

  const columns: ColumnType<SampledRecord>[] = [
    { title: "Record ID", dataIndex: "record_id", width: 90 },
    { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
    { title: "Status", dataIndex: "status", render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag> },
  ];

  return (
    <Drawer
      title={lotDetail ? `Assign to QC: ${lotDetail.name}` : "Assign to QC"}
      open={!!lotId}
      onClose={onClose}
      width={680}
    >
      {!lotDetail ? (
        <Spin />
      ) : lotDetail.status !== "qc_in_progress" ? (
        <Empty description="Apply a sample from Settings before assigning records to QC." />
      ) : (
        <>
          <Card title={`Assign ${sampledRecords.length} Sampled Records to QC Agents`} style={{ marginBottom: 16 }}>
            <Row gutter={16} align="middle" style={{ marginBottom: 16 }}>
              <Col>
                <Typography.Text>Batch size</Typography.Text>
                <br />
                <InputNumber
                  min={1}
                  max={sampledRecords.length || 1}
                  value={batchSize}
                  onChange={(v) => setBatchSize(v ?? 25)}
                  style={{ width: 100, marginTop: 4 }}
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

            <Typography.Text strong>QC Agents</Typography.Text>
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              placeholder="Search and select QC agents"
              style={{ width: "100%", marginTop: 8, marginBottom: 16 }}
              value={selectedAgents}
              onChange={setSelectedAgents}
              options={staff.map((agent) => ({ label: agent.full_name, value: agent.id }))}
            />

            {batchPreview.length > 0 && (
              <>
                <Typography.Text type="secondary">
                  Preview — {batchPreview.length} batch{batchPreview.length !== 1 ? "es" : ""} across{" "}
                  {selectedAgents.length} agent{selectedAgents.length !== 1 ? "s" : ""}:
                </Typography.Text>
                <Row gutter={[8, 8]} style={{ marginTop: 8, marginBottom: 16 }}>
                  {selectedAgents.map((agentId) => {
                    const agentBatches = batchPreview.filter((b) => b.agentId === agentId);
                    const agentName = staff.find((s) => s.id === agentId)?.full_name ?? agentId;
                    const total = agentBatches.reduce((n, b) => n + b.recordIds.length, 0);
                    return (
                      <Col key={agentId}>
                        <Tag>
                          {agentName} — {agentBatches.length} batch{agentBatches.length !== 1 ? "es" : ""}, {total} records
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
              disabled={!selectedDocType || !selectedAgents.length}
              onClick={() => createQcBatchesMutation.mutate()}
            >
              Create QC Batches
            </Button>
          </Card>

          <Card title="Sampled Records">
            <Table rowKey="record_id" size="small" dataSource={sampledRecords} pagination={{ pageSize: 20 }} columns={columns} />
          </Card>
        </>
      )}
    </Drawer>
  );
}
