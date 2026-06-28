import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, InputNumber,
  Select, Tag, Tabs, Space, message, Popconfirm,
  Typography, Card, Descriptions, Row, Col, Tooltip, Upload,
} from "antd";
import type { UploadRequestOption } from "rc-upload/lib/interface";
import {
  PlusOutlined, RightOutlined, UploadOutlined, EditOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Batch, DocRecord, DocumentType } from "@shared/types";

const SCHEMA_TEMPLATE = JSON.stringify(
  {
    title: "Document",
    type: "object",
    properties: {
      title: { type: "string", title: "Document Title" },
      date: { type: "string", format: "date", title: "Document Date" },
      category: {
        type: "string",
        title: "Category",
        enum: ["Invoice", "Receipt", "Contract"],
      },
      notes: { type: "string", title: "Notes" },
    },
    required: ["title"],
  },
  null,
  2
);

const BATCH_STATUS_COLOR: Record<string, string> = {
  draft: "default",
  submitted: "blue",
  indexing: "processing",
  qa_review: "gold",
  customer_qc: "purple",
  passed: "green",
  rejected: "red",
};

const RECORD_STATUS_COLOR: Record<string, string> = {
  pending: "default",
  indexing: "processing",
  indexed: "cyan",
  qa_pending: "gold",
  qa_passed: "green",
  qa_failed: "red",
  qc_pending: "purple",
  qc_passed: "green",
  qc_failed: "red",
};

const BATCH_NEXT_ACTION: Record<string, { label: string; endpoint: string } | null> = {
  draft: { label: "Submit for Indexing", endpoint: "submit" },
  submitted: { label: "Advance → Indexing", endpoint: "advance-indexing" },
  indexing: { label: "Advance → QA Review", endpoint: "advance-qa" },
  qa_review: { label: "Advance → Customer QC", endpoint: "advance-customer-qc" },
  customer_qc: null,
  passed: null,
  rejected: null,
};

interface Props { projectId: number; isAdmin?: boolean }

export default function BatchManager({ projectId, isAdmin = false }: Props) {
  const qc = useQueryClient();

  // --- Document Types ---
  const [dtOpen, setDtOpen] = useState(false);
  const [dtForm] = Form.useForm();
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const { data: docTypes = [] } = useQuery<DocumentType[]>({
    queryKey: ["doc-types", projectId],
    queryFn: () => api.get(`/projects/${projectId}/document-types`).then((r) => r.data),
  });

  const createDt = useMutation({
    mutationFn: (values: { name: string; schema_text: string }) => {
      const json_schema = JSON.parse(values.schema_text);
      return api.post(`/document-types?project_id=${projectId}`, { name: values.name, json_schema }).then((r) => r.data);
    },
    onSuccess: () => {
      message.success("Document type created");
      qc.invalidateQueries({ queryKey: ["doc-types", projectId] });
      setDtOpen(false);
      dtForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create document type");
    },
  });

  // --- Batches ---
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchForm] = Form.useForm();
  const [selectedBatch, setSelectedBatch] = useState<Batch | null>(null);
  const [addRecordsOpen, setAddRecordsOpen] = useState(false);
  const [addRecordsForm] = Form.useForm();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [schemaEditOpen, setSchemaEditOpen] = useState(false);
  const [schemaEditTarget, setSchemaEditTarget] = useState<DocumentType | null>(null);
  const [schemaEditForm] = Form.useForm();
  const [schemaEditError, setSchemaEditError] = useState<string | null>(null);

  const { data: batches = [], isLoading: batchesLoading } = useQuery<Batch[]>({
    queryKey: ["batches", projectId],
    queryFn: () => api.get(`/projects/${projectId}/batches`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const createBatch = useMutation({
    mutationFn: (values: { name: string; document_type_id: number }) =>
      api.post("/batches", { ...values, project_id: projectId }).then((r) => r.data),
    onSuccess: () => {
      message.success("Batch created");
      qc.invalidateQueries({ queryKey: ["batches", projectId] });
      setBatchOpen(false);
      batchForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create batch");
    },
  });

  const advanceBatch = useMutation({
    mutationFn: ({ id, endpoint }: { id: number; endpoint: string }) =>
      api.post(`/batches/${id}/${endpoint}`).then((r) => r.data),
    onSuccess: (updated: Batch) => {
      message.success(`Batch moved to: ${updated.status}`);
      qc.invalidateQueries({ queryKey: ["batches", projectId] });
      if (selectedBatch?.id === updated.id) setSelectedBatch(updated);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to advance batch");
    },
  });

  // --- Records ---
  const { data: records = [], isLoading: recordsLoading } = useQuery<DocRecord[]>({
    queryKey: ["records", selectedBatch?.id],
    queryFn: () => api.get(`/batches/${selectedBatch!.id}/records`).then((r) => r.data),
    enabled: !!selectedBatch,
    refetchInterval: 10_000,
  });

  const addRecords = useMutation({
    mutationFn: (count: number) =>
      api.post(`/batches/${selectedBatch!.id}/records`, { count }).then((r) => r.data),
    onSuccess: (created: DocRecord[]) => {
      message.success(`Added ${created.length} record${created.length > 1 ? "s" : ""}`);
      qc.invalidateQueries({ queryKey: ["records", selectedBatch?.id] });
      setAddRecordsOpen(false);
      addRecordsForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to add records");
    },
  });

  const updateDocType = useMutation({
    mutationFn: (values: { name: string; schema_text: string }) => {
      const json_schema = JSON.parse(values.schema_text);
      return api
        .patch(`/document-types/${schemaEditTarget!.id}`, { name: values.name, json_schema })
        .then((r) => r.data);
    },
    onSuccess: () => {
      message.success("Document type updated");
      qc.invalidateQueries({ queryKey: ["doc-types", projectId] });
      setSchemaEditOpen(false);
      setSchemaEditTarget(null);
      schemaEditForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to update document type");
    },
  });

  const openSchemaEdit = (dt: DocumentType) => {
    setSchemaEditTarget(dt);
    schemaEditForm.setFieldsValue({
      name: dt.name,
      schema_text: JSON.stringify(dt.json_schema, null, 2),
    });
    setSchemaEditError(null);
    setSchemaEditOpen(true);
  };

  // Upload each file: create record → get presigned URL → PUT to S3 → confirm
  // On any failure, delete the orphaned record so it doesn't persist.
  const handleCustomUpload = async (opts: UploadRequestOption) => {
    const { file, onProgress, onSuccess, onError } = opts;
    let recordId: number | null = null;
    try {
      // 1. Create a single blank record in this batch
      const [record] = await api
        .post(`/batches/${selectedBatch!.id}/records`, { count: 1 })
        .then((r) => r.data as DocRecord[]);
      recordId = record.id;

      // 2. Get a presigned upload URL
      const { upload_url, s3_key } = await api
        .post(`/records/${record.id}/upload-url`)
        .then((r) => r.data as { upload_url: string; s3_key: string });

      // 3. PUT directly to S3/MinIO (no Authorization header — presigned URL handles auth)
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", upload_url);
        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            onProgress?.({ percent: Math.round((e.loaded / e.total) * 100) });
          }
        });
        xhr.onload = () => (xhr.status < 400 ? resolve() : reject(new Error(`S3 PUT failed: ${xhr.status}`)));
        xhr.onerror = () => reject(new Error("Network error during upload"));
        xhr.send(file as File);
      });

      // 4. Confirm the upload so the record's file_reference is set
      await api.patch(`/records/${record.id}/confirm-upload`, { s3_key });

      onSuccess?.({ record_id: record.id });
    } catch (err) {
      // Clean up the orphaned record so it doesn't appear as a stale pending entry
      if (recordId !== null) {
        api.delete(`/records/${recordId}`).catch(() => {});
      }
      onError?.(err as Error);
    }
  };

  const batchColumns: ColumnsType<Batch> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      render: (v: string) => <Tag color={BATCH_STATUS_COLOR[v] ?? "default"}>{v.replace(/_/g, " ")}</Tag>,
    },
    {
      title: "Doc Type",
      dataIndex: "document_type_id",
      key: "dt",
      render: (id: number) => docTypes.find((d) => d.id === id)?.name ?? `ID ${id}`,
    },
    {
      title: "",
      key: "action",
      render: (_: unknown, batch: Batch) => {
        const next = BATCH_NEXT_ACTION[batch.status];
        return (
          <Space>
            <Button
              size="small"
              icon={<RightOutlined />}
              onClick={() => setSelectedBatch(batch)}
            >
              Records
            </Button>
            {next && (
              <Popconfirm
                title={`${next.label}?`}
                onConfirm={() => advanceBatch.mutate({ id: batch.id, endpoint: next.endpoint })}
              >
                <Button size="small" type="primary">
                  {next.label}
                </Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  const recordColumns: ColumnsType<DocRecord> = [
    { title: "ID", dataIndex: "id", width: 70 },
    {
      title: "Status",
      dataIndex: "status",
      render: (v: string) => <Tag color={RECORD_STATUS_COLOR[v] ?? "default"}>{v.replace(/_/g, " ")}</Tag>,
    },
    { title: "Version", dataIndex: "current_version", render: (v: number) => `v${v}`, width: 80 },
    {
      title: "File",
      dataIndex: "file_reference",
      render: (v: string | null) => v
        ? <Typography.Text ellipsis style={{ maxWidth: 200 }}>{v.split("/").pop()}</Typography.Text>
        : <Tag>No file</Tag>,
    },
    {
      title: "Lock",
      dataIndex: "locked_by",
      width: 80,
      render: (v: number | null) => v ? <Tag color="orange">Locked</Tag> : <Tag color="green">Free</Tag>,
    },
  ];

  const dtColumns: ColumnsType<DocumentType> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Schema Title",
      key: "schema_title",
      render: (_: unknown, dt: DocumentType) => {
        const schema = dt.json_schema as { title?: string; type?: string };
        return (
          <Space>
            {schema.title && <Typography.Text strong>{schema.title}</Typography.Text>}
            {schema.type && <Tag color="blue">{schema.type}</Tag>}
          </Space>
        );
      },
    },
    {
      title: "Fields",
      key: "fields",
      render: (_: unknown, dt: DocumentType) => {
        const schema = dt.json_schema as {
          properties?: Record<string, { type?: string; title?: string; format?: string; enum?: unknown[] }>;
        };
        const props = schema.properties ?? {};
        return (
          <Space wrap>
            {Object.entries(props).map(([key, def]) => {
              const label = def.title ?? key;
              const typeTag = def.enum ? "enum" : def.format ?? def.type ?? "any";
              return (
                <Tag key={key} style={{ marginBottom: 2 }}>
                  {label} <Typography.Text type="secondary" style={{ fontSize: 11 }}>({typeTag})</Typography.Text>
                </Tag>
              );
            })}
          </Space>
        );
      },
    },
    {
      title: "Required",
      key: "required",
      width: 160,
      render: (_: unknown, dt: DocumentType) => {
        const req = (dt.json_schema as { required?: string[] }).required ?? [];
        return req.length ? (
          <Space wrap>{req.map((f) => <Tag key={f} color="orange">{f}</Tag>)}</Space>
        ) : (
          <Typography.Text type="secondary">none</Typography.Text>
        );
      },
    },
    { title: "ID", dataIndex: "id", width: 60 },
    ...(isAdmin ? [{
      title: "",
      key: "actions",
      width: 120,
      render: (_: unknown, dt: DocumentType) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => openSchemaEdit(dt)}>
          Edit Schema
        </Button>
      ),
    }] : []),
  ];

  const canModify = selectedBatch?.status === "draft";

  return (
    <>
      <Tabs
        defaultActiveKey="batches"
        items={[
          {
            key: "batches",
            label: "Batches",
            children: (
              <>
                <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 16, fontWeight: 600 }}>Batches for this project</span>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setBatchOpen(true)}>
                    New Batch
                  </Button>
                </Space>

                <Table
                  dataSource={batches}
                  columns={batchColumns}
                  rowKey="id"
                  loading={batchesLoading}
                  size="middle"
                  rowClassName={(b) => b.id === selectedBatch?.id ? "ant-table-row-selected" : ""}
                />

                {selectedBatch && (
                  <Card
                    style={{ marginTop: 24 }}
                    title={
                      <Space>
                        <span>Records — {selectedBatch.name}</span>
                        <Tag color={BATCH_STATUS_COLOR[selectedBatch.status] ?? "default"}>
                          {selectedBatch.status}
                        </Tag>
                      </Space>
                    }
                    extra={
                      <Space>
                        {isAdmin && (
                          <>
                            <Button
                              icon={<UploadOutlined />}
                              onClick={() => setUploadOpen(true)}
                              disabled={!canModify}
                              type="primary"
                            >
                              Upload Images
                            </Button>
                            <Button
                              icon={<PlusOutlined />}
                              onClick={() => setAddRecordsOpen(true)}
                              disabled={!canModify}
                            >
                              Add Blank Records
                            </Button>
                          </>
                        )}
                        <Button size="small" onClick={() => setSelectedBatch(null)}>
                          Close
                        </Button>
                      </Space>
                    }
                  >
                    <Row gutter={16} style={{ marginBottom: 16 }}>
                      <Col>
                        <Descriptions size="small" column={4}>
                          <Descriptions.Item label="Total">{records.length}</Descriptions.Item>
                          <Descriptions.Item label="With file">
                            {records.filter((r) => r.file_reference).length}
                          </Descriptions.Item>
                          <Descriptions.Item label="Pending">
                            {records.filter((r) => r.status === "pending").length}
                          </Descriptions.Item>
                          <Descriptions.Item label="Indexed">
                            {records.filter((r) => r.status === "indexed").length}
                          </Descriptions.Item>
                        </Descriptions>
                      </Col>
                    </Row>
                    <Table
                      dataSource={records}
                      columns={recordColumns}
                      rowKey="id"
                      loading={recordsLoading}
                      size="small"
                      pagination={{ pageSize: 20 }}
                    />
                  </Card>
                )}
              </>
            ),
          },
          {
            key: "doctypes",
            label: "Document Types",
            children: (
              <>
                <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 16, fontWeight: 600 }}>Document Types</span>
                  {isAdmin && (
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setDtOpen(true)}>
                      New Document Type
                    </Button>
                  )}
                </Space>
                <Table dataSource={docTypes} columns={dtColumns} rowKey="id" size="middle" />
              </>
            ),
          },
        ]}
      />

      {/* Upload images modal */}
      <Modal
        title={`Upload Images — ${selectedBatch?.name}`}
        open={uploadOpen}
        footer={
          <Button
            onClick={() => {
              setUploadOpen(false);
              setUploading(false);
              qc.invalidateQueries({ queryKey: ["records", selectedBatch?.id] });
            }}
          >
            Done
          </Button>
        }
        onCancel={() => {
          if (uploading) return;
          setUploadOpen(false);
          qc.invalidateQueries({ queryKey: ["records", selectedBatch?.id] });
        }}
        destroyOnClose
        width={560}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
          Each file creates one record. Files are uploaded directly to S3 and linked to their record automatically.
          Accepted formats: JPEG, PNG, TIFF, PDF, BMP, WebP.
        </Typography.Paragraph>
        <Upload.Dragger
          multiple
          accept=".jpg,.jpeg,.png,.tif,.tiff,.pdf,.bmp,.webp"
          customRequest={handleCustomUpload}
          onChange={(info) => {
            const inProgress = info.fileList.some((f) => f.status === "uploading");
            setUploading(inProgress);
            if (info.file.status === "done") {
              message.success(`${info.file.name} uploaded`);
            } else if (info.file.status === "error") {
              message.error(`${info.file.name} failed — ${info.file.error?.message ?? "unknown error"}`);
            }
          }}
          style={{ padding: "24px 0" }}
        >
          <p className="ant-upload-drag-icon">
            <UploadOutlined style={{ fontSize: 40, color: "#1677ff" }} />
          </p>
          <p className="ant-upload-text">Click or drag files here to upload</p>
          <p className="ant-upload-hint">
            Drop multiple files at once. Each file = one record in this batch.
          </p>
        </Upload.Dragger>
      </Modal>

      {/* Create batch modal */}
      <Modal
        title="Create Batch"
        open={batchOpen}
        onOk={() => batchForm.submit()}
        onCancel={() => { setBatchOpen(false); batchForm.resetFields(); }}
        confirmLoading={createBatch.isPending}
        destroyOnClose
      >
        <Form form={batchForm} layout="vertical" onFinish={createBatch.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="name" label="Batch Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="document_type_id" label="Document Type" rules={[{ required: true }]}>
            <Select
              options={docTypes.map((d) => ({ value: d.id, label: d.name }))}
              placeholder="Select document type"
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Add blank records modal */}
      <Modal
        title={`Add Blank Records to "${selectedBatch?.name}"`}
        open={addRecordsOpen}
        onOk={() => addRecordsForm.submit()}
        onCancel={() => { setAddRecordsOpen(false); addRecordsForm.resetFields(); }}
        confirmLoading={addRecords.isPending}
        destroyOnClose
      >
        <Form
          form={addRecordsForm}
          layout="vertical"
          onFinish={(v) => addRecords.mutate(v.count)}
          style={{ marginTop: 12 }}
          initialValues={{ count: 10 }}
        >
          <Form.Item
            name="count"
            label="Number of blank records to add"
            rules={[{ required: true, type: "number", min: 1, max: 500 }]}
          >
            <InputNumber min={1} max={500} style={{ width: "100%" }} />
          </Form.Item>
          <Typography.Text type="secondary">
            Records created without files. Use "Upload Images" to attach files per record later.
          </Typography.Text>
        </Form>
      </Modal>

      {/* Edit document type / schema modal */}
      <Modal
        title={`Edit Schema — ${schemaEditTarget?.name}`}
        open={schemaEditOpen}
        onOk={() => schemaEditForm.submit()}
        onCancel={() => {
          setSchemaEditOpen(false);
          setSchemaEditTarget(null);
          schemaEditForm.resetFields();
          setSchemaEditError(null);
        }}
        confirmLoading={updateDocType.isPending}
        destroyOnClose
        width={620}
      >
        <Form
          form={schemaEditForm}
          layout="vertical"
          style={{ marginTop: 12 }}
          onFinish={(v) => {
            try {
              const parsed = JSON.parse(v.schema_text);
              if (parsed.type !== "object") {
                setSchemaEditError('Schema must have "type": "object" at root for rjsf to render the form.');
                return;
              }
              setSchemaEditError(null);
              updateDocType.mutate(v);
            } catch {
              setSchemaEditError("Invalid JSON — please fix before saving.");
            }
          }}
        >
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="schema_text"
            label={
              <Space>
                <span>JSON Schema</span>
                <Tooltip title='Must be a valid JSON Schema with "type": "object" at root. Fields go inside "properties".'>
                  <Typography.Text type="secondary" style={{ cursor: "help" }}>(?)</Typography.Text>
                </Tooltip>
              </Space>
            }
            rules={[{ required: true }]}
          >
            <Input.TextArea
              rows={16}
              style={{ fontFamily: "monospace", fontSize: 12 }}
              onChange={() => setSchemaEditError(null)}
            />
          </Form.Item>
          {schemaEditError && (
            <Typography.Text type="danger">{schemaEditError}</Typography.Text>
          )}
        </Form>
      </Modal>

      {/* Create document type modal */}
      <Modal
        title="Create Document Type"
        open={dtOpen}
        onOk={() => dtForm.submit()}
        onCancel={() => { setDtOpen(false); dtForm.resetFields(); setSchemaError(null); }}
        confirmLoading={createDt.isPending}
        destroyOnClose
        width={600}
      >
        <Form
          form={dtForm}
          layout="vertical"
          style={{ marginTop: 12 }}
          onFinish={(v) => {
            try {
              const parsed = JSON.parse(v.schema_text);
              if (parsed.type !== "object") {
                setSchemaError('Schema must have "type": "object" at root for rjsf to render the form.');
                return;
              }
              setSchemaError(null);
              createDt.mutate(v);
            } catch {
              setSchemaError("Invalid JSON — please fix before saving.");
            }
          }}
        >
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="schema_text"
            label={
              <Space>
                <span>JSON Schema</span>
                <Tooltip title="Defines the indexing form fields. Use the template as a starting point.">
                  <Typography.Text type="secondary" style={{ cursor: "help" }}>(?)</Typography.Text>
                </Tooltip>
              </Space>
            }
            rules={[{ required: true }]}
          >
            <Input.TextArea
              rows={12}
              style={{ fontFamily: "monospace", fontSize: 12 }}
              onChange={() => setSchemaError(null)}
            />
          </Form.Item>
          {schemaError && (
            <Typography.Text type="danger">{schemaError}</Typography.Text>
          )}
          <Button
            size="small"
            onClick={() => dtForm.setFieldValue("schema_text", SCHEMA_TEMPLATE)}
          >
            Use template
          </Button>
        </Form>
      </Modal>
    </>
  );
}
