import {
  Alert, Button, Card, Checkbox, Col, Drawer, Empty, InputNumber, Row, Select,
  Slider, Space, Spin, Table, Tag, Typography, message,
} from "antd";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { AvailableStaff, DocumentType, Lot, LotDetail } from "@shared/types";

interface Props {
  projectId: number;
  role: string;
}

const LOT_STATUS_COLOR: Record<string, string> = {
  draft: "default",
  released: "blue",
  qc_in_progress: "processing",
  passed: "success",
  failed: "error",
  remediation: "warning",
};

export default function CustomerLotManager({ projectId, role }: Props) {
  const isSupervisor = role === "customer_supervisor";
  const qc = useQueryClient();
  const [detailLotId, setDetailLotId] = useState<number | undefined>();
  const [sampleRate, setSampleRate] = useState<number>(10);
  const [selectedAgents, setSelectedAgents] = useState<number[]>([]);
  const [batchSize, setBatchSize] = useState<number>(25);
  const [selectedDocType, setSelectedDocType] = useState<number | undefined>();

  const { data: lots = [], isLoading: lotLoading } = useQuery<Lot[]>({
    queryKey: ["customer-lots", projectId],
    queryFn: () => api.get(`/lots/project/${projectId}`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const { data: lotDetail } = useQuery<LotDetail>({
    queryKey: ["lot-detail", detailLotId],
    queryFn: () => api.get(`/lots/${detailLotId}`).then((r) => r.data),
    enabled: !!detailLotId,
    refetchInterval: 10_000,
  });

  const { data: staff = [] } = useQuery<AvailableStaff[]>({
    queryKey: ["qc-staff", projectId],
    queryFn: () => api.get(`/projects/${projectId}/qc-agents`).then((r) => r.data),
  });

  const { data: docTypes = [] } = useQuery<DocumentType[]>({
    queryKey: ["doc-types", projectId],
    queryFn: () => api.get(`/projects/${projectId}/document-types`).then((r) => r.data),
  });

  const applySampleMutation = useMutation({
    mutationFn: ({ lotId, rate }: { lotId: number; rate: number }) =>
      api.post(`/lots/${lotId}/sample`, { sample_rate: rate / 100 }),
    onSuccess: () => {
      message.success("Sample applied — records selected for QC");
      qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
      qc.invalidateQueries({ queryKey: ["customer-lots", projectId] });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to apply sample");
    },
  });

  // Split sampled records into chunks of batchSize, assign round-robin to selected agents
  const batchPreview = useMemo(() => {
    if (!lotDetail || !selectedAgents.length || batchSize < 1) return [];
    const sampled = lotDetail.records.filter((r) => r.is_sampled).map((r) => r.record_id);
    const chunks: { agentId: number; recordIds: number[] }[] = [];
    for (let i = 0; i < sampled.length; i += batchSize) {
      const agentId = selectedAgents[chunks.length % selectedAgents.length];
      chunks.push({ agentId, recordIds: sampled.slice(i, i + batchSize) });
    }
    return chunks;
  }, [lotDetail, selectedAgents, batchSize]);

  const createQcBatchesMutation = useMutation({
    mutationFn: ({ lotId }: { lotId: number }) => {
      if (!selectedDocType) throw new Error("Select a document type");
      if (!selectedAgents.length) throw new Error("Select at least one QC agent");
      if (!batchPreview.length) throw new Error("No sampled records to assign");
      return api.post(`/lots/${lotId}/qc-batches`, {
        project_id: projectId,
        document_type_id: selectedDocType,
        assignments: batchPreview.map((b) => ({
          agent_id: b.agentId,
          record_ids: b.recordIds,
        })),
      });
    },
    onSuccess: () => {
      message.success("QC batches created and assigned");
      qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
      setSelectedAgents([]);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? String((e as Error).message));
    },
  });

  const remediationMutation = useMutation({
    mutationFn: (lotId: number) => api.post(`/lots/${lotId}/send-for-remediation`),
    onSuccess: () => {
      message.success("Lot sent for remediation");
      qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
      qc.invalidateQueries({ queryKey: ["customer-lots", projectId] });
    },
    onError: () => message.error("Failed to send for remediation"),
  });

  const visibleLots = lots.filter((l) =>
    ["released", "qc_in_progress", "passed", "failed", "remediation"].includes(l.status)
  );

  const columns: ColumnType<Lot>[] = [
    { title: "ID", dataIndex: "id", width: 60 },
    { title: "Name", dataIndex: "name" },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => (
        <Tag color={LOT_STATUS_COLOR[s] ?? "default"}>{s.replace(/_/g, " ")}</Tag>
      ),
    },
    {
      title: "Sample",
      dataIndex: "sample_rate",
      render: (v: number | null, row: Lot) =>
        v !== null
          ? `${(v * 100).toFixed(0)}% (${row.sample_size} records)`
          : "—",
    },
    {
      title: "Accuracy",
      dataIndex: "accuracy_rate",
      render: (v: number | null) =>
        v !== null ? (
          <Tag color={v >= 0.9 ? "success" : "error"}>{(v * 100).toFixed(1)}%</Tag>
        ) : "—",
    },
    ...(isSupervisor
      ? [{
          title: "",
          key: "action",
          render: (_: unknown, lot: Lot) => (
            <Button size="small" onClick={() => setDetailLotId(lot.id)}>
              Manage
            </Button>
          ),
        }]
      : []),
  ];

  const sampledRecords = lotDetail?.records.filter((r) => r.is_sampled) ?? [];

  return (
    <div>
      <Typography.Title level={4}>Lots</Typography.Title>

      {lotLoading ? (
        <Spin />
      ) : visibleLots.length === 0 ? (
        <Empty description="No lots released to your organisation yet" />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={visibleLots}
          size="middle"
          pagination={{ pageSize: 20 }}
        />
      )}

      <Drawer
        title={lotDetail ? `Lot: ${lotDetail.name}` : "Lot"}
        open={!!detailLotId}
        onClose={() => setDetailLotId(undefined)}
        width={680}
      >
        {!lotDetail ? (
          <Spin />
        ) : (
          <>
            <Row gutter={8} align="middle" style={{ marginBottom: 16 }}>
              <Col>
                <Tag color={LOT_STATUS_COLOR[lotDetail.status] ?? "default"} style={{ fontSize: 14 }}>
                  {lotDetail.status.replace(/_/g, " ")}
                </Tag>
              </Col>
              {lotDetail.accuracy_rate !== null && (
                <Col>
                  <Typography.Text strong>
                    Accuracy: {(lotDetail.accuracy_rate * 100).toFixed(1)}%
                  </Typography.Text>
                </Col>
              )}
            </Row>

            {lotDetail.accuracy_rate !== null && lotDetail.accuracy_rate < 0.9 && (
              <Alert
                type="error"
                style={{ marginBottom: 16 }}
                message={`Lot failed QC (${(lotDetail.accuracy_rate * 100).toFixed(1)}% accuracy — threshold 90%)`}
                action={
                  <Button
                    danger
                    size="small"
                    loading={remediationMutation.isPending}
                    onClick={() => remediationMutation.mutate(lotDetail.id)}
                  >
                    Send for Remediation
                  </Button>
                }
              />
            )}

            {/* Sampling */}
            {lotDetail.status === "released" && (
              <Card title="Apply QC Sample" style={{ marginBottom: 16 }}>
                <Typography.Text>
                  Total records in lot: <strong>{lotDetail.records.length}</strong>
                </Typography.Text>
                <Row align="middle" gutter={16} style={{ marginTop: 12 }}>
                  <Col flex="auto">
                    <Slider
                      min={1}
                      max={100}
                      value={sampleRate}
                      onChange={setSampleRate}
                      marks={{ 5: "5%", 10: "10%", 20: "20%", 50: "50%", 100: "100%" }}
                    />
                  </Col>
                  <Col>
                    <InputNumber
                      min={1}
                      max={100}
                      value={sampleRate}
                      onChange={(v) => setSampleRate(v ?? 10)}
                      formatter={(v) => `${v}%`}
                      parser={(v) => Number(v?.replace("%", "")) as 1}
                      style={{ width: 80 }}
                    />
                  </Col>
                </Row>
                <Typography.Text type="secondary">
                  Will select ~{Math.ceil((sampleRate / 100) * lotDetail.records.length)} records
                </Typography.Text>
                <div style={{ marginTop: 12 }}>
                  <Button
                    type="primary"
                    loading={applySampleMutation.isPending}
                    onClick={() =>
                      applySampleMutation.mutate({ lotId: lotDetail.id, rate: sampleRate })
                    }
                  >
                    Apply Sample
                  </Button>
                </div>
              </Card>
            )}

            {/* QC batch assignment for sampled records */}
            {lotDetail.status === "qc_in_progress" && sampledRecords.length > 0 && (
              <Card
                title={`Assign ${sampledRecords.length} Sampled Records to QC Agents`}
                style={{ marginBottom: 16 }}
              >
                <Row gutter={16} align="middle" style={{ marginBottom: 16 }}>
                  <Col>
                    <Typography.Text>Batch size</Typography.Text>
                    <br />
                    <InputNumber
                      min={1}
                      max={sampledRecords.length}
                      value={batchSize}
                      onChange={(v) => setBatchSize(v ?? 25)}
                      style={{ width: 100, marginTop: 4 }}
                    />
                  </Col>
                  <Col>
                    <Typography.Text>Document type</Typography.Text>
                    <br />
                    <Select
                      placeholder="Select type"
                      style={{ width: 200, marginTop: 4 }}
                      options={docTypes.map((d) => ({ label: d.name, value: d.id }))}
                      onChange={setSelectedDocType}
                      value={selectedDocType}
                    />
                  </Col>
                </Row>

                <Typography.Text strong>Select QC Agents</Typography.Text>
                <Row gutter={[16, 8]} style={{ marginTop: 8, marginBottom: 16 }}>
                  {staff.map((agent) => (
                    <Col key={agent.id} xs={24} sm={12} md={8}>
                      <Checkbox
                        checked={selectedAgents.includes(agent.id)}
                        onChange={(e) =>
                          setSelectedAgents((prev) =>
                            e.target.checked
                              ? [...prev, agent.id]
                              : prev.filter((id) => id !== agent.id)
                          )
                        }
                      >
                        {agent.full_name}
                      </Checkbox>
                    </Col>
                  ))}
                </Row>

                {batchPreview.length > 0 && (
                  <>
                    <Typography.Text type="secondary">
                      Preview — {batchPreview.length} batch{batchPreview.length !== 1 ? "es" : ""} across {selectedAgents.length} agent{selectedAgents.length !== 1 ? "s" : ""}:
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
                  onClick={() => createQcBatchesMutation.mutate({ lotId: lotDetail.id })}
                >
                  Create QC Batches
                </Button>
              </Card>
            )}

            {/* All records */}
            <Card title="All Records in Lot">
              <Table
                rowKey="record_id"
                size="small"
                dataSource={lotDetail.records}
                pagination={{ pageSize: 20 }}
                columns={[
                  { title: "Record ID", dataIndex: "record_id", width: 90 },
                  { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
                  {
                    title: "Status",
                    dataIndex: "status",
                    render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag>,
                  },
                  {
                    title: "QC Sample",
                    dataIndex: "is_sampled",
                    render: (v: boolean) => v ? <Tag color="blue">Sampled</Tag> : "—",
                  },
                ]}
              />
            </Card>
          </>
        )}
      </Drawer>
    </div>
  );
}
