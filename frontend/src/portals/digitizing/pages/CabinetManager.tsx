import {
  Button, Card, Col, Empty, Form, Input, Modal, Row, Spin,
  Table, Tabs, Tag, Typography, Upload, message,
} from "antd";
import { InboxOutlined, PlusOutlined, EditOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { Cabinet, CabinetRecord, DocumentType } from "@shared/types";

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
  const [ingestJsonOpen, setIngestJsonOpen] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [idField, setIdField] = useState("id");
  const [recordSearch, setRecordSearch] = useState("");

  // Document type state
  const [dtModalOpen, setDtModalOpen] = useState(false);
  const [editingDt, setEditingDt] = useState<DocumentType | null>(null);
  const [dtName, setDtName] = useState("");
  const [dtSchemaText, setDtSchemaText] = useState("");

  const { data: cabinets = [], isLoading: cabLoading } = useQuery<Cabinet[]>({
    queryKey: ["cabinets", projectId],
    queryFn: () => api.get(`/cabinets/project/${projectId}`).then((r) => r.data),
  });

  // One cabinet per project — always use the first
  const cabinet = cabinets[0];

  const { data: records = [], isLoading: recLoading } = useQuery<CabinetRecord[]>({
    queryKey: ["cabinet-records", cabinet?.id],
    queryFn: () => api.get(`/cabinets/${cabinet!.id}/records`).then((r) => r.data),
    enabled: !!cabinet,
    refetchInterval: 10_000,
  });

  const ingestJsonMutation = useMutation({
    mutationFn: async ({ records, idField }: { records: unknown[]; idField: string }) => {
      await api.post(
        `/cabinets/${cabinet!.id}/ingest-json`,
        { id_field: idField, records }
      );
    },
    onSuccess: () => {
      message.success("Records ingested from JSON");
      qc.invalidateQueries({ queryKey: ["cabinet-records", cabinet?.id] });
      setIngestJsonOpen(false);
      setJsonText("");
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Ingest failed");
    },
  });

  const uploadImageMutation = useMutation({
    mutationFn: async ({ file }: { file: File }) => {
      const urlRes = await api.post(
        `/cabinets/${cabinet!.id}/upload-url?filename=${encodeURIComponent(file.name)}`
      );
      const { upload_url, key } = urlRes.data;
      await fetch(upload_url, { method: "PUT", body: file });
      await api.patch(
        `/cabinets/${cabinet!.id}/confirm-upload`,
        null,
        { params: { original_filename: file.name, s3_key: key } }
      );
    },
    onSuccess: () => {
      message.success("Image uploaded");
      qc.invalidateQueries({ queryKey: ["cabinet-records", cabinet?.id] });
    },
    onError: () => message.error("Image upload failed"),
  });

  const { data: documentTypes = [], isLoading: dtLoading } = useQuery<DocumentType[]>({
    queryKey: ["document-types", projectId],
    queryFn: () => api.get(`/projects/${projectId}/document-types`).then((r) => r.data),
  });

  const saveDtMutation = useMutation({
    mutationFn: async () => {
      let schema: unknown;
      try { schema = JSON.parse(dtSchemaText); } catch {
        throw new Error("Invalid JSON schema");
      }
      if (editingDt) {
        await api.patch(`/document-types/${editingDt.id}`, { name: dtName, json_schema: schema });
      } else {
        await api.post("/document-types", { name: dtName, json_schema: schema }, { params: { project_id: projectId } });
      }
    },
    onSuccess: () => {
      message.success(editingDt ? "Document type updated" : "Document type created");
      qc.invalidateQueries({ queryKey: ["document-types", projectId] });
      closeDtModal();
    },
    onError: (e: unknown) => {
      const err = e as { message?: string; response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? err.message ?? "Save failed");
    },
  });

  const openCreateDt = () => {
    setEditingDt(null);
    setDtName("");
    setDtSchemaText("");
    setDtModalOpen(true);
  };

  const openEditDt = (dt: DocumentType) => {
    setEditingDt(dt);
    setDtName(dt.name);
    setDtSchemaText(JSON.stringify(dt.json_schema, null, 2));
    setDtModalOpen(true);
  };

  const closeDtModal = () => {
    setDtModalOpen(false);
    setEditingDt(null);
    setDtName("");
    setDtSchemaText("");
  };

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

  if (cabLoading) return <Spin />;

  if (!cabinet) {
    return (
      <Empty description="No cabinet found for this project. The cabinet is created automatically when a project is set up." />
    );
  }

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Typography.Title level={4} style={{ margin: 0 }}>
            Cabinet: {cabinet.name}
          </Typography.Title>
          <Typography.Text type="secondary">{records.length} records total</Typography.Text>
        </Col>
      </Row>

      {recLoading ? (
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
              {
                key: "document-types",
                label: `Document Types (${documentTypes.length})`,
                children: (
                  <>
                    <Row justify="end" style={{ marginBottom: 12 }}>
                      <Button type="primary" icon={<PlusOutlined />} onClick={openCreateDt}>
                        New Document Type
                      </Button>
                    </Row>
                    {dtLoading ? (
                      <Spin />
                    ) : documentTypes.length === 0 ? (
                      <Empty description="No document types yet. Create one to define the indexing form for agents." />
                    ) : (
                      <Table
                        rowKey="id"
                        size="small"
                        dataSource={documentTypes}
                        pagination={false}
                        columns={[
                          { title: "ID", dataIndex: "id", width: 60 },
                          { title: "Name", dataIndex: "name" },
                          {
                            title: "Fields",
                            render: (_: unknown, dt: DocumentType) => {
                              const props = (dt.json_schema as { properties?: Record<string, unknown> })?.properties;
                              return props ? Object.keys(props).length : "—";
                            },
                          },
                          {
                            title: "Actions",
                            key: "actions",
                            render: (_: unknown, dt: DocumentType) => (
                              <Button
                                size="small"
                                icon={<EditOutlined />}
                                onClick={() => openEditDt(dt)}
                              >
                                Edit
                              </Button>
                            ),
                          },
                        ]}
                      />
                    )}
                  </>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* Document Type Modal */}
      <Modal
        title={editingDt ? `Edit: ${editingDt.name}` : "New Document Type"}
        open={dtModalOpen}
        onCancel={closeDtModal}
        onOk={() => saveDtMutation.mutate()}
        confirmLoading={saveDtMutation.isPending}
        okText="Save"
        width={680}
      >
        <Form layout="vertical">
          <Form.Item label="Name" required>
            <Input
              value={dtName}
              onChange={(e) => setDtName(e.target.value)}
              placeholder='e.g. "Birth Certificate"'
            />
          </Form.Item>
          <Form.Item
            label="JSON Schema"
            extra="Standard JSON Schema object. Use properties to define the fields agents will fill in."
            required
          >
            <Input.TextArea
              rows={16}
              value={dtSchemaText}
              onChange={(e) => setDtSchemaText(e.target.value)}
              placeholder={'{\n  "type": "object",\n  "properties": {\n    "surname": { "type": "string", "title": "Surname" }\n  }\n}'}
              style={{ fontFamily: "monospace", fontSize: 13 }}
            />
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
