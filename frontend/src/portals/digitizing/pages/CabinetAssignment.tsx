import {
  Badge, Button, Card, Col, Empty, InputNumber, Row, Select, Spin,
  Table, Tag, Typography, message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { AvailableStaff, Cabinet, CabinetRecord, DocumentType, Shift } from "@shared/types";

interface Props {
  projectId: number;
}

const BATCH_STATUS_COLOR: Record<string, string> = {
  indexing: "processing",
  qa_review: "warning",
  complete: "success",
  draft: "default",
};

export default function CabinetAssignment({ projectId }: Props) {
  const qc = useQueryClient();
  const [selectedCabinet, setSelectedCabinet] = useState<number | undefined>();
  const [selectedShift, setSelectedShift] = useState<number | undefined>();
  const [selectedDocType, setSelectedDocType] = useState<number | undefined>();
  const [agentAllocations, setAgentAllocations] = useState<Record<number, number>>({});
  const [qaAssign, setQaAssign] = useState<Record<number, number>>({});

  const { data: cabinets = [] } = useQuery<Cabinet[]>({
    queryKey: ["cabinets", projectId],
    queryFn: () => api.get(`/api/cabinets/project/${projectId}`).then((r) => r.data),
  });

  const { data: shifts = [] } = useQuery<Shift[]>({
    queryKey: ["project-shifts", projectId],
    queryFn: () => api.get(`/projects/${projectId}/shifts`).then((r) => r.data),
  });

  const { data: docTypes = [] } = useQuery<DocumentType[]>({
    queryKey: ["doc-types", projectId],
    queryFn: () => api.get(`/projects/${projectId}/document-types`).then((r) => r.data),
  });

  const { data: records = [], isLoading: recLoading } = useQuery<CabinetRecord[]>({
    queryKey: ["cabinet-records", selectedCabinet, "pending"],
    queryFn: () =>
      api.get(`/api/cabinets/${selectedCabinet}/records?status=pending`).then((r) => r.data),
    enabled: !!selectedCabinet,
  });

  const { data: staff = [] } = useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", projectId, selectedShift],
    queryFn: () =>
      api.get(`/projects/${projectId}/available-staff`, { params: { shift_id: selectedShift } })
        .then((r) => r.data),
    enabled: !!selectedShift,
  });

  // Batches for this cabinet
  const { data: batches = [], isLoading: batchLoading } = useQuery<{
    id: number; name: string; status: string; batch_type: string;
  }[]>({
    queryKey: ["cabinet-batches", selectedCabinet],
    queryFn: () =>
      api.get(`/batches`, { params: { cabinet_id: selectedCabinet } }).then((r) => r.data),
    enabled: !!selectedCabinet,
    refetchInterval: 15_000,
  });

  const createBatchesMutation = useMutation({
    mutationFn: async () => {
      if (!selectedCabinet || !selectedDocType) throw new Error("Select cabinet and document type");
      const pendingRecords = records.filter((r) => r.status === "pending");
      if (!pendingRecords.length) throw new Error("No pending records");

      const staffWithAlloc = staff.filter((s) => (agentAllocations[s.id] ?? 0) > 0);
      if (!staffWithAlloc.length) throw new Error("Set allocation counts for at least one agent");

      // Distribute records to agents based on their allocation counts
      let offset = 0;
      for (const agent of staffWithAlloc) {
        const count = agentAllocations[agent.id] ?? 0;
        const slice = pendingRecords.slice(offset, offset + count);
        if (!slice.length) continue;
        await api.post(`/api/cabinets/${selectedCabinet}/batches`, {
          project_id: projectId,
          document_type_id: selectedDocType,
          record_ids: slice.map((r) => r.id),
          agent_id: agent.id,
        });
        offset += count;
      }
    },
    onSuccess: () => {
      message.success("Indexing batches created");
      qc.invalidateQueries({ queryKey: ["cabinet-records", selectedCabinet] });
      qc.invalidateQueries({ queryKey: ["cabinet-batches", selectedCabinet] });
      setAgentAllocations({});
    },
    onError: (e: Error) => message.error(e.message || "Failed to create batches"),
  });

  const assignQaMutation = useMutation({
    mutationFn: ({ batchId, agentId }: { batchId: number; agentId: number }) =>
      api.patch(`/api/cabinets/batches/${batchId}/assign-qa`, { agent_id: agentId }),
    onSuccess: () => {
      message.success("QA agent assigned");
      qc.invalidateQueries({ queryKey: ["cabinet-batches", selectedCabinet] });
    },
    onError: () => message.error("Failed to assign QA agent"),
  });

  const pendingCount = records.filter((r) => r.status === "pending").length;
  const totalAllocated = Object.values(agentAllocations).reduce((s, n) => s + n, 0);

  return (
    <div>
      <Typography.Title level={4}>Cabinet Assignment</Typography.Title>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col>
          <Select
            placeholder="Select cabinet"
            style={{ width: 240 }}
            options={cabinets.map((c) => ({ label: c.name, value: c.id }))}
            onChange={setSelectedCabinet}
            value={selectedCabinet}
          />
        </Col>
        <Col>
          <Select
            placeholder="Select shift"
            style={{ width: 180 }}
            options={shifts.map((s) => ({ label: s.name, value: s.id }))}
            onChange={setSelectedShift}
            value={selectedShift}
          />
        </Col>
        <Col>
          <Select
            placeholder="Document type"
            style={{ width: 200 }}
            options={docTypes.map((d) => ({ label: d.name, value: d.id }))}
            onChange={setSelectedDocType}
            value={selectedDocType}
          />
        </Col>
      </Row>

      {!selectedCabinet ? (
        <Empty description="Select a cabinet" />
      ) : (
        <>
          {/* Agent allocation panel */}
          {pendingCount > 0 && staff.length > 0 && (
            <Card
              title={`Allocate ${pendingCount} pending records to agents`}
              style={{ marginBottom: 16 }}
              extra={
                <Button
                  type="primary"
                  loading={createBatchesMutation.isPending}
                  disabled={!selectedDocType || totalAllocated === 0}
                  onClick={() => createBatchesMutation.mutate()}
                >
                  Create Batches ({totalAllocated} / {pendingCount} allocated)
                </Button>
              }
            >
              <Row gutter={16}>
                {staff.map((agent) => (
                  <Col key={agent.id} xs={12} sm={8} md={6} style={{ marginBottom: 8 }}>
                    <Typography.Text>{agent.full_name}</Typography.Text>
                    <InputNumber
                      min={0}
                      max={pendingCount}
                      value={agentAllocations[agent.id] ?? 0}
                      onChange={(v) =>
                        setAgentAllocations((prev) => ({ ...prev, [agent.id]: v ?? 0 }))
                      }
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                ))}
              </Row>
            </Card>
          )}

          {/* Batch list */}
          <Card title="Batches">
            {batchLoading ? (
              <Spin />
            ) : batches.length === 0 ? (
              <Empty description="No batches yet for this cabinet" />
            ) : (
              <Table
                rowKey="id"
                size="small"
                dataSource={batches}
                pagination={false}
                columns={[
                  { title: "ID", dataIndex: "id", width: 70 },
                  { title: "Name", dataIndex: "name" },
                  { title: "Type", dataIndex: "batch_type", render: (v) => <Tag>{v}</Tag> },
                  {
                    title: "Status",
                    dataIndex: "status",
                    render: (s: string) => (
                      <Tag color={BATCH_STATUS_COLOR[s] ?? "default"}>{s.replace(/_/g, " ")}</Tag>
                    ),
                  },
                  {
                    title: "Assign QA Agent",
                    key: "qa",
                    render: (_: unknown, batch: { id: number; status: string }) =>
                      batch.status === "qa_review" ? (
                        <Row gutter={8}>
                          <Col>
                            <Select
                              size="small"
                              placeholder="QA agent"
                              style={{ width: 160 }}
                              options={staff
                                .filter((s) => s.role === "de_qa_agent")
                                .map((s) => ({ label: s.full_name, value: s.id }))}
                              value={qaAssign[batch.id]}
                              onChange={(v) => setQaAssign((prev) => ({ ...prev, [batch.id]: v }))}
                            />
                          </Col>
                          <Col>
                            <Button
                              size="small"
                              type="primary"
                              disabled={!qaAssign[batch.id]}
                              loading={assignQaMutation.isPending}
                              onClick={() =>
                                assignQaMutation.mutate({
                                  batchId: batch.id,
                                  agentId: qaAssign[batch.id],
                                })
                              }
                            >
                              Assign
                            </Button>
                          </Col>
                        </Row>
                      ) : null,
                  },
                ]}
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
