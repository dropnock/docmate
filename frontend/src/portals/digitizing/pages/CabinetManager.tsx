import {
  Badge, Button, Card, Col, Empty, Form, Input, Modal, Row, Select, Spin,
  Table, Tabs, Tag, Typography, Upload, message,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { useState, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { Cabinet, CabinetRecord, Project } from "@shared/types";

interface Props {
  projectId: number;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  indexing: "processing",
  indexed: "success",
  qa_pending: "warning",
  qa_passed: "success",
  qa_failed: "error",
  qc_pending: "warning",
  qc_passed: "success",
  qc_failed: "error",
};

export default function CabinetManager({ projectId }: Props) {
  const qc = useQueryClient();
  const [selectedCabinet, setSelectedCabinet] = useState<number | undefined>();
  const [createOpen, setCreateOpen] = useState(false);
  const [ingestJsonOpen, setIngestJsonOpen] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [idField, setIdField] = useState("id");
  const [recordSearch, setRecordSearch] = useState("");
  const [form] = Form.useForm();

  const { data: project } = useQuery<Project>({
    queryKey: ["project", projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
  });

  const { data: cabinets = [], isLoading: cabLoading } = useQuery<Cabinet[]>({
    queryKey: ["cabinets", projectId],
    queryFn: () => api.get(`/api/cabinets/project/${projectId}`).then((r) => r.data),
  });

  const { data: records = [], isLoading: recLoading } = useQuery<CabinetRecord[]>({
    queryKey: ["cabinet-records", selectedCabinet],
    queryFn: () => api.get(`/api/cabinets/${selectedCabinet}/records`).then((r) => r.data),
    enabled: !!selectedCabinet,
    refetchInterval: 10_000,
  });

  const createMutation = useMutation({
    mutationFn: (values: { name: string; description?: string }) =>
      api.post("/api/cabinets", { project_id: projectId, ...values }),
    onSuccess: () => {
      message.success("Cabinet created");
      qc.invalidateQueries({ queryKey: ["cabinets", projectId] });
      setCreateOpen(false);
      form.resetFields();
    },
    onError: () => message.error("Failed to create cabinet"),
  });

  const ingestJsonMutation = useMutation({
    mutationFn: async ({ records, idField }: { records: unknown[]; idField: string }) => {
      // We need a placeholder batch; for pure-JSON ingestion we create a stub batch via legacy endpoint
      // then ingest into cabinet
      const batchRes = await api.post("/batches", {
        project_id: projectId,
        document_type_id: 1, // default; can be changed later
        name: `JSON Ingest ${new Date().toISOString().slice(0, 16)}`,
      });
      await api.post(
        `/api/cabinets/${selectedCabinet}/ingest-json?batch_id=${batchRes.data.id}`,
        { id_field: idField, records }
      );
    },
    onSuccess: () => {
      message.success("Records ingested from JSON");
      qc.invalidateQueries({ queryKey: ["cabinet-records", selectedCabinet] });
      setIngestJsonOpen(false);
      setJsonText("");
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Ingest failed");
    },
  });

  const uploadImageMutation = useMutation({
    mutationFn: async ({ file, recordId }: { file: File; recordId?: number }) => {
      const rid = recordId ?? 0;
      const urlRes = await api.post(
        `/api/cabinets/${selectedCabinet}/records/${rid}/upload-url?filename=${encodeURIComponent(file.name)}`
      );
      const { upload_url, key } = urlRes.data;
      await fetch(upload_url, { method: "PUT", body: file });
      await api.patch(
        `/api/cabinets/${selectedCabinet}/records/${rid}/confirm-upload`,
        null,
        { params: { original_filename: file.name, s3_key: key } }
      );
    },
    onSuccess: () => {
      message.success("Image uploaded");
      qc.invalidateQueries({ queryKey: ["cabinet-records", selectedCabinet] });
    },
    onError: () => message.error("Image upload failed"),
  });

  const handleIngestJson = () => {
    let parsed: unknown[];
    try {
      const val = JSON.parse(jsonText);
      parsed = Array.isArray(val) ? val : [val];
    } catch {
      message.error("Invalid JSON — must be an array or a single object");
      return;
    }
    ingestJsonMutation.mutate({ records: parsed, idField });
  };

  const filteredRecords = records.filter((r) => {
    const q = recordSearch.toLowerCase();
    return (
      (r.source_identifier ?? "").toLowerCase().includes(q) ||
      (r.original_filename ?? "").toLowerCase().includes(q) ||
      r.status.toLowerCase().includes(q) ||
      String(r.id).includes(q)
    );
  });

  const recordColumns: ColumnType<CabinetRecord>[] = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? <Tag>—</Tag> },
    {
      title: "Filename",
      dataIndex: "original_filename",
      render: (v) => v ? (
        <Typography.Text ellipsis style={{ maxWidth: 200 }}>{v}</Typography.Text>
      ) : <Tag color="default">—</Tag>,
    },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? "default"}>{s.replace(/_/g, " ")}</Tag>
      ),
    },
    {
      title: "Image",
      dataIndex: "has_image",
      render: (v: boolean) => v ? <Tag color="green">Yes</Tag> : <Tag color="orange">No</Tag>,
    },
    {
      title: "Data",
      dataIndex: "has_data",
      render: (v: boolean) => v ? <Tag color="green">Yes</Tag> : <Tag color="orange">No</Tag>,
    },
    { title: "Version", dataIndex: "current_version", render: (v) => `v${v}` },
  ];

  return (
    <div>
      <Typography.Title level={4}>Cabinets</Typography.Title>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col>
          <Select
            placeholder="Select cabinet"
            style={{ width: 260 }}
            loading={cabLoading}
            options={cabinets.map((c) => ({ label: c.name, value: c.id }))}
            onChange={setSelectedCabinet}
            value={selectedCabinet}
          />
        </Col>
        <Col>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            New Cabinet
          </Button>
        </Col>
      </Row>

      {!selectedCabinet ? (
        <Empty description="Select a cabinet to view and manage records" />
      ) : recLoading ? (
        <Spin />
      ) : (
        <Card>
          <Tabs
            items={[
              {
                key: "records",
                label: `Records (${records.length})`,
                children: (
                  <>
                    <Row gutter={8} style={{ marginBottom: 12 }}>
                      <Col>
                        <Input.Search
                          placeholder="Search by identifier, filename, status…"
                          allowClear
                          onChange={(e) => setRecordSearch(e.target.value)}
                          style={{ width: 320 }}
                        />
                      </Col>
                      <Col>
                        <Button onClick={() => setIngestJsonOpen(true)}>
                          Ingest JSON
                        </Button>
                      </Col>
                    </Row>
                    <Table
                      rowKey="id"
                      columns={recordColumns}
                      dataSource={filteredRecords}
                      size="small"
                      pagination={{ pageSize: 50 }}
                    />
                  </>
                ),
              },
              {
                key: "images",
                label: "Upload Images",
                children: (
                  <Upload.Dragger
                    multiple
                    accept=".tif,.tiff,.jpg,.jpeg,.png,.pdf"
                    showUploadList={false}
                    customRequest={({ file, onSuccess, onError }) => {
                      uploadImageMutation.mutate(
                        { file: file as File },
                        {
                          onSuccess: () => onSuccess?.("ok"),
                          onError: (e) => onError?.(e as Error),
                        }
                      );
                    }}
                    style={{ padding: 32 }}
                  >
                    <p className="ant-upload-drag-icon">
                      <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">
                      Drag images here or click to upload
                    </p>
                    <p className="ant-upload-hint">
                      Supported: TIFF, JPG, PNG, PDF. The original filename is retained
                      and used to auto-link to JSON-ingested records by identifier.
                    </p>
                  </Upload.Dragger>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* Create Cabinet Modal */}
      <Modal
        title="New Cabinet"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={(v) => createMutation.mutate(v)}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Ingest JSON Modal */}
      <Modal
        title="Ingest JSON Records"
        open={ingestJsonOpen}
        onCancel={() => setIngestJsonOpen(false)}
        onOk={handleIngestJson}
        confirmLoading={ingestJsonMutation.isPending}
        width={640}
      >
        <Typography.Paragraph type="secondary">
          Paste a JSON array (or single object). Each item becomes one record.
          Specify the field that contains the unique identifier so images can be auto-linked.
        </Typography.Paragraph>
        <Form layout="vertical">
          <Form.Item label="Identifier field name">
            <Input
              value={idField}
              onChange={(e) => setIdField(e.target.value)}
              placeholder='e.g. "id" or "record_number"'
            />
          </Form.Item>
          <Form.Item label="JSON">
            <Input.TextArea
              rows={12}
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              placeholder='[{"id": "123", "name": "..."}, ...]'
              style={{ fontFamily: "monospace" }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
