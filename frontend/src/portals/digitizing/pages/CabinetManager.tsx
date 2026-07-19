import {
  Button, Card, Col, Empty, Form, Input, Modal, Progress, Row, Segmented, Spin,
  Table, Tabs, Tag, Typography, Upload, message,
} from "antd";
import { UploadCloud, Plus, Pencil, Eye, Image as ImageIcon } from "lucide-react";
import { useState, useMemo, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { useCabinets } from "@shared/hooks/useCabinets";
import { useDocumentTypes } from "@shared/hooks/useDocumentTypes";
import StatusDot from "@shared/components/StatusDot";
import type { CabinetRecord, DocumentType } from "@shared/types";

interface Props {
  projectId: number;
}

const RECORD_STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  indexing: "Indexing",
  indexed: "Indexed",
  qa_pending: "QA Pending",
  qa_passed: "QA Passed",
  qa_failed: "QA Failed",
  qc_pending: "QC Pending",
  qc_passed: "QC Passed",
  qc_failed: "QC Failed",
  disqualified: "Disqualified",
  withdrawn: "Withdrawn",
  ineligible: "Ineligible",
  excluded: "Excluded",
};

const RECORD_STATUS_FILLED = new Set(["indexed", "qa_passed", "qc_passed"]);

export default function CabinetManager({ projectId }: Props) {
  const qc = useQueryClient();
  const [ingestJsonOpen, setIngestJsonOpen] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [idField, setIdField] = useState("id");
  const [recordSearch, setRecordSearch] = useState("");

  // Images tab state
  const [imageSearch, setImageSearch] = useState("");
  const [imageFilter, setImageFilter] = useState("all");
  const [loadingViewId, setLoadingViewId] = useState<number | null>(null);

  // Document type state
  const [dtModalOpen, setDtModalOpen] = useState(false);
  const [editingDt, setEditingDt] = useState<DocumentType | null>(null);
  const [dtName, setDtName] = useState("");
  const [dtSchemaText, setDtSchemaText] = useState("");

  const { data: cabinets = [], isLoading: cabLoading } = useCabinets(projectId);

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

  // Batch upload progress — one summary message at the end instead of a
  // toast per file.
  const [uploadStats, setUploadStats] = useState<{ total: number; done: number; failed: number } | null>(null);

  const uploadImageMutation = useMutation({
    mutationFn: async ({ file }: { file: File }) => {
      const formData = new FormData();
      formData.append("file", file);
      await api.post(`/cabinets/${cabinet!.id}/upload`, formData);
    },
  });

  useEffect(() => {
    if (!uploadStats || uploadStats.total === 0) return;
    if (uploadStats.done + uploadStats.failed !== uploadStats.total) return;
    qc.invalidateQueries({ queryKey: ["cabinet-records", cabinet?.id] });
    const { total, done, failed } = uploadStats;
    if (failed === 0) {
      message.success(`${done} of ${total} image${total > 1 ? "s" : ""} uploaded successfully`);
    } else {
      message.warning(`${done} of ${total} image${total > 1 ? "s" : ""} uploaded — ${failed} failed`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadStats]);

  const { data: documentTypes = [], isLoading: dtLoading } = useDocumentTypes(projectId);

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

  const imageRecords = useMemo(() => records.filter((r) => r.has_image), [records]);

  const filteredImages = useMemo(() => {
    const q = imageSearch.toLowerCase();
    return imageRecords.filter((r) => {
      const matchesSearch =
        !q ||
        (r.original_filename ?? "").toLowerCase().includes(q) ||
        (r.source_identifier ?? "").toLowerCase().includes(q);
      const matchesFilter =
        imageFilter === "all" ||
        (imageFilter === "linked" && r.has_data) ||
        (imageFilter === "unlinked" && !r.has_data);
      return matchesSearch && matchesFilter;
    });
  }, [imageRecords, imageSearch, imageFilter]);

  const handleViewImage = async (recordId: number) => {
    if (!cabinet) return;
    setLoadingViewId(recordId);
    try {
      const { data } = await api.get(`/cabinets/${cabinet.id}/records/${recordId}/view-url`);
      window.open(data.url, "_blank", "noopener,noreferrer");
    } catch {
      message.error("Could not generate view URL");
    } finally {
      setLoadingViewId(null);
    }
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
        <StatusDot filled={RECORD_STATUS_FILLED.has(s)} label={RECORD_STATUS_LABEL[s] ?? s.replace(/_/g, " ")} />
      ),
    },
    {
      title: "Image",
      dataIndex: "has_image",
      render: (v: boolean) => <StatusDot filled={v} label={v ? "Yes" : "No"} />,
    },
    {
      title: "Data",
      dataIndex: "has_data",
      render: (v: boolean) => <StatusDot filled={v} label={v ? "Yes" : "No"} />,
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
                label: (
                  <span>
                    <ImageIcon size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
                    Images ({imageRecords.length})
                  </span>
                ),
                children: (
                  <>
                    <Upload.Dragger
                      multiple
                      accept=".tif,.tiff,.jpg,.jpeg,.png,.pdf"
                      showUploadList={false}
                      beforeUpload={(file, fileList) => {
                        if (file === fileList[0]) {
                          setUploadStats({ total: fileList.length, done: 0, failed: 0 });
                        }
                        return true;
                      }}
                      customRequest={({ file, onSuccess, onError }) => {
                        // mutateAsync, not mutate — CabinetManager fires one
                        // customRequest per file in rapid succession for a
                        // multi-file drop, and uploadImageMutation is a single
                        // shared useMutation() instance. mutate()'s per-call
                        // {onSuccess, onError} are dispatched through the
                        // mutation's shared observer, which each subsequent
                        // mutate() call re-targets at its own mutation —
                        // silently dropping the callback for every upload
                        // that was still in flight when the next one started.
                        // mutateAsync's returned promise is tied to that
                        // call's own mutation execution, so it resolves
                        // correctly regardless of later concurrent calls.
                        uploadImageMutation.mutateAsync({ file: file as File }).then(
                          () => {
                            onSuccess?.("ok");
                            setUploadStats((prev) => prev && { ...prev, done: prev.done + 1 });
                          },
                          (e) => {
                            onError?.(e as Error);
                            setUploadStats((prev) => prev && { ...prev, failed: prev.failed + 1 });
                          }
                        );
                      }}
                      style={{ padding: 16, marginBottom: 12 }}
                    >
                      <p className="ant-upload-drag-icon" style={{ margin: "4px 0" }}>
                        <UploadCloud size={28} color="#1E40AF" />
                      </p>
                      <p className="ant-upload-text">Drag images here or click to upload</p>
                      <p className="ant-upload-hint">
                        Supported: TIFF, JPG, PNG, PDF. The original filename is retained
                        and used to auto-link to JSON-ingested records by identifier.
                      </p>
                    </Upload.Dragger>

                    {uploadStats && uploadStats.done + uploadStats.failed < uploadStats.total && (
                      <div style={{ marginBottom: 12 }}>
                        <Typography.Text type="secondary">
                          Uploading {uploadStats.done + uploadStats.failed} of {uploadStats.total}…
                        </Typography.Text>
                        <Progress
                          percent={Math.round(((uploadStats.done + uploadStats.failed) / uploadStats.total) * 100)}
                          size="small"
                          status={uploadStats.failed > 0 ? "exception" : "active"}
                        />
                      </div>
                    )}

                    <Row gutter={8} style={{ marginBottom: 12 }} align="middle">
                      <Col>
                        <Input.Search
                          placeholder="Search by filename or identifier…"
                          allowClear
                          value={imageSearch}
                          onChange={(e) => setImageSearch(e.target.value)}
                          style={{ width: 300 }}
                        />
                      </Col>
                      <Col>
                        <Segmented
                          options={[
                            { label: "All", value: "all" },
                            { label: "Linked to record", value: "linked" },
                            { label: "Unlinked", value: "unlinked" },
                          ]}
                          value={imageFilter}
                          onChange={(v) => setImageFilter(v as string)}
                        />
                      </Col>
                    </Row>
                    {imageRecords.length === 0 ? (
                      <Empty description="No images in this cabinet yet. Drag files above to upload." />
                    ) : (
                      <Table
                        rowKey="id"
                        size="small"
                        dataSource={filteredImages}
                        pagination={{ pageSize: 50, showSizeChanger: false }}
                        locale={{ emptyText: "No images match your filters." }}
                        columns={[
                          {
                            title: "Filename",
                            dataIndex: "original_filename",
                            render: (v: string | null) => (
                              <Typography.Text ellipsis style={{ maxWidth: 260 }}>
                                {v ?? <Tag>—</Tag>}
                              </Typography.Text>
                            ),
                          },
                          {
                            title: "Linked Record",
                            dataIndex: "source_identifier",
                            render: (v: string | null, r: CabinetRecord) =>
                              v ? (
                                <StatusDot filled={r.has_data} label={r.has_data ? v : `${v} (no data)`} />
                              ) : (
                                <span style={{ color: "#64748B" }}>—</span>
                              ),
                          },
                          {
                            title: "Status",
                            dataIndex: "status",
                            render: (s: string) => (
                              <StatusDot filled={RECORD_STATUS_FILLED.has(s)} label={RECORD_STATUS_LABEL[s] ?? s.replace(/_/g, " ")} />
                            ),
                          },
                          {
                            title: "",
                            key: "view",
                            width: 80,
                            render: (_: unknown, r: CabinetRecord) => (
                              <Button
                                size="small"
                                icon={<Eye size={14} />}
                                loading={loadingViewId === r.id}
                                onClick={() => handleViewImage(r.id)}
                              >
                                View
                              </Button>
                            ),
                          },
                        ]}
                      />
                    )}
                  </>
                ),
              },
              {
                key: "document-types",
                label: `Document Types (${documentTypes.length})`,
                children: (
                  <>
                    <Row justify="end" style={{ marginBottom: 12 }}>
                      <Button type="primary" icon={<Plus size={16} />} onClick={openCreateDt}>
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
                                icon={<Pencil size={14} />}
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
            extra={
              <>
                Standard JSON Schema object. Use properties to define the fields agents will fill in.
                Add a top-level <code>"required": [...]</code> array to mark fields mandatory — enforced
                on Submit &amp; Complete only, not Save Progress. Set <code>"x-hidden": true</code> on a
                property to remove it from the form entirely, or <code>"x-disabled": true</code> to show
                it read-only.
              </>
            }
            required
          >
            <Input.TextArea
              rows={16}
              value={dtSchemaText}
              onChange={(e) => setDtSchemaText(e.target.value)}
              placeholder={'{\n  "type": "object",\n  "properties": {\n    "surname": { "type": "string", "title": "Surname" },\n    "internal_code": { "type": "string", "x-hidden": true }\n  },\n  "required": ["surname"]\n}'}
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
