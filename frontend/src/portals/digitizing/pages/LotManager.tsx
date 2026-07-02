import {
  Button, Card, Col, Checkbox, Drawer, Empty, Form, Input, Row,
  Space, Spin, Table, Tag, Typography, message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { Cabinet, CabinetRecord, Lot, LotDetail } from "@shared/types";

interface Props {
  projectId: number;
}

const LOT_STATUS_COLOR: Record<string, string> = {
  draft: "default",
  released: "blue",
  qc_in_progress: "processing",
  passed: "success",
  failed: "error",
  remediation: "warning",
};

export default function LotManager({ projectId }: Props) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailLotId, setDetailLotId] = useState<number | undefined>();
  const [selectedRecordIds, setSelectedRecordIds] = useState<number[]>([]);
  const [lotName, setLotName] = useState("");
  const [lotDescription, setLotDescription] = useState("");

  const { data: lots = [], isLoading: lotLoading } = useQuery<Lot[]>({
    queryKey: ["lots", projectId],
    queryFn: () => api.get(`/lots/project/${projectId}`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  // One cabinet per project — auto-load
  const { data: cabinets = [] } = useQuery<Cabinet[]>({
    queryKey: ["cabinets", projectId],
    queryFn: () => api.get(`/cabinets/project/${projectId}`).then((r) => r.data),
  });
  const cabinet = cabinets[0];

  // QA-passed records in the project cabinet
  const { data: qaPassedRecords = [], isLoading: recLoading } = useQuery<CabinetRecord[]>({
    queryKey: ["cabinet-records", cabinet?.id, "qa_passed"],
    queryFn: () =>
      api.get(`/cabinets/${cabinet!.id}/records?status=qa_passed`).then((r) => r.data),
    enabled: !!cabinet && createOpen,
  });

  const { data: lotDetail } = useQuery<LotDetail>({
    queryKey: ["lot-detail", detailLotId],
    queryFn: () => api.get(`/lots/${detailLotId}`).then((r) => r.data),
    enabled: !!detailLotId,
    refetchInterval: 10_000,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.post("/lots", {
        project_id: projectId,
        name: lotName,
        description: lotDescription || undefined,
        record_ids: selectedRecordIds,
      }),
    onSuccess: () => {
      message.success("Lot created");
      qc.invalidateQueries({ queryKey: ["lots", projectId] });
      setCreateOpen(false);
      setLotName("");
      setLotDescription("");
      setSelectedRecordIds([]);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create lot");
    },
  });

  const releaseMutation = useMutation({
    mutationFn: (lotId: number) => api.post(`/lots/${lotId}/release`),
    onSuccess: () => {
      message.success("Lot released to customer");
      qc.invalidateQueries({ queryKey: ["lots", projectId] });
      qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
    },
    onError: () => message.error("Failed to release lot"),
  });

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
      title: "Accuracy",
      dataIndex: "accuracy_rate",
      render: (v: number | null) =>
        v !== null ? `${(v * 100).toFixed(1)}%` : "—",
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, lot: Lot) => (
        <Space>
          <Button size="small" onClick={() => setDetailLotId(lot.id)}>
            View
          </Button>
          {lot.status === "draft" && (
            <Button
              size="small"
              type="primary"
              loading={releaseMutation.isPending}
              onClick={() => releaseMutation.mutate(lot.id)}
            >
              Release
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const recordColumns: ColumnType<LotDetail["records"][0]>[] = [
    { title: "Record ID", dataIndex: "record_id", width: 90 },
    { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
    { title: "Filename", dataIndex: "original_filename", render: (v) => v ?? "—" },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag>,
    },
    {
      title: "Sampled",
      dataIndex: "is_sampled",
      render: (v: boolean) => v ? <Tag color="blue">Yes</Tag> : "—",
    },
  ];

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Typography.Title level={4} style={{ margin: 0 }}>Lots</Typography.Title>
        </Col>
        <Col>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            Create Lot
          </Button>
        </Col>
      </Row>

      {lotLoading ? (
        <Spin />
      ) : lots.length === 0 ? (
        <Empty description="No lots yet. Create one from QA-passed cabinet records." />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={lots}
          size="middle"
          pagination={{ pageSize: 20 }}
        />
      )}

      {/* Create Lot Drawer */}
      <Drawer
        title="Create Lot"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={560}
        extra={
          <Button
            type="primary"
            loading={createMutation.isPending}
            disabled={!lotName || selectedRecordIds.length === 0}
            onClick={() => createMutation.mutate()}
          >
            Create ({selectedRecordIds.length} records)
          </Button>
        }
      >
        <Form layout="vertical">
          <Form.Item label="Lot Name" required>
            <Input value={lotName} onChange={(e) => setLotName(e.target.value)} />
          </Form.Item>
          <Form.Item label="Description">
            <Input.TextArea rows={2} value={lotDescription} onChange={(e) => setLotDescription(e.target.value)} />
          </Form.Item>
        </Form>

        {recLoading ? (
          <Spin />
        ) : qaPassedRecords.length === 0 ? (
          <Empty description="No QA-passed records in this project's cabinet yet." />
        ) : (
          <>
            <Row justify="space-between" style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary">
                {qaPassedRecords.length} QA-passed records available
              </Typography.Text>
              <Space>
                <Button
                  size="small"
                  onClick={() => setSelectedRecordIds(qaPassedRecords.map((r) => r.id))}
                >
                  Select All
                </Button>
                <Button size="small" onClick={() => setSelectedRecordIds([])}>
                  Clear
                </Button>
              </Space>
            </Row>
            <Table
              rowKey="id"
              size="small"
              dataSource={qaPassedRecords}
              pagination={{ pageSize: 20 }}
              rowSelection={{
                selectedRowKeys: selectedRecordIds,
                onChange: (keys) => setSelectedRecordIds(keys as number[]),
              }}
              columns={[
                { title: "ID", dataIndex: "id", width: 70 },
                { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
                { title: "Filename", dataIndex: "original_filename", render: (v) => v ?? "—" },
              ]}
            />
          </>
        )}
      </Drawer>

      {/* Lot Detail Drawer */}
      <Drawer
        title={lotDetail ? `Lot: ${lotDetail.name}` : "Lot Detail"}
        open={!!detailLotId}
        onClose={() => setDetailLotId(undefined)}
        width={640}
      >
        {!lotDetail ? (
          <Spin />
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
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
              {lotDetail.status === "draft" && (
                <Col>
                  <Button
                    type="primary"
                    size="small"
                    loading={releaseMutation.isPending}
                    onClick={() => releaseMutation.mutate(lotDetail.id)}
                  >
                    Release to Customer
                  </Button>
                </Col>
              )}
            </Row>
            <Table
              rowKey="record_id"
              size="small"
              dataSource={lotDetail.records}
              columns={recordColumns}
              pagination={{ pageSize: 20 }}
            />
          </>
        )}
      </Drawer>
    </div>
  );
}
