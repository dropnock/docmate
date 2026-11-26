import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input,
  DatePicker, Select, message, Space, Row, Col, Divider, Typography, Drawer, Alert,
} from "antd";
import { Plus, FolderOpen, Pencil, History } from "lucide-react";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import api from "@shared/api/client";
import { useProjects } from "@shared/hooks/useProjects";
import StatusDot from "@shared/components/StatusDot";
import { aqlConfigKey, getAqlConfig, projectHistoryKey } from "@shared/api/aql";
import {
  AQL_OPTIONS, CRITICAL_AQL_OPTIONS, DEFAULT_CLASS_AQLS, ZERO_TOLERANCE,
} from "@shared/components/DefectClasses";
import type { Project, Organization } from "@shared/types";
import ProjectHistory from "../components/ProjectHistory";

const STATUS_LABEL: Record<string, string> = {
  ready: "Ready",
  provisioning: "Provisioning",
  error: "Error",
};

interface Props {
  onOpen?: (projectId: number) => void;
}

/** Form values -> request body: dates as YYYY-MM-DD, zero tolerance as null. */
function toBody(values: Record<string, unknown>) {
  const { critical_aql, proposed_end_date, ...rest } = values;
  return {
    ...rest,
    proposed_end_date:
      (proposed_end_date as { format?: (s: string) => string } | null)?.format?.("YYYY-MM-DD") ?? null,
    ...(critical_aql === undefined ? {} : { critical_aql: critical_aql === ZERO_TOLERANCE ? null : critical_aql }),
  };
}

/** Critical / major / minor AQL pickers, shared by the create and edit forms. */
function AqlFields() {
  return (
    <>
      <Divider orientation="left" orientationMargin={0} style={{ marginTop: 4 }}>
        Acceptance quality limits
      </Divider>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8, fontSize: 12 }}>
        Used for the outgoing inspection and the customer&apos;s QC of each lot. Errors in identifier fields are
        critical; others are major or minor by the error-type agreement in the customer&apos;s lot settings.
      </Typography.Paragraph>
      <Row gutter={12}>
        <Col span={8}>
          <Form.Item name="critical_aql" label="Critical" rules={[{ required: true }]}>
            <Select options={CRITICAL_AQL_OPTIONS} data-aql-select="critical" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="major_aql" label="Major" rules={[{ required: true }]}>
            <Select options={AQL_OPTIONS} data-aql-select="major" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="minor_aql" label="Minor" rules={[{ required: true }]}>
            <Select options={AQL_OPTIONS} data-aql-select="minor" />
          </Form.Item>
        </Col>
      </Row>
    </>
  );
}

const CREATE_DEFAULTS = {
  critical_aql: ZERO_TOLERANCE,
  major_aql: DEFAULT_CLASS_AQLS.major,
  minor_aql: DEFAULT_CLASS_AQLS.minor,
};

export default function ProjectsManager({ onOpen }: Props) {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Project | null>(null);
  const [historyTarget, setHistoryTarget] = useState<Project | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: projects = [], isLoading } = useProjects();

  const { data: orgs = [] } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
  });

  const custOrgs = orgs.filter((o) => o.type === "customer");
  const orgById = Object.fromEntries(orgs.map((o) => [o.id, o]));

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/projects", toBody(values)).then((r) => r.data),
    onSuccess: () => {
      message.success("Project created");
      qc.invalidateQueries({ queryKey: ["projects"] });
      setCreateOpen(false);
      createForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create project");
    },
  });

  const edit = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.patch(`/projects/${editTarget!.id}`, toBody(values)).then((r) => r.data),
    onSuccess: () => {
      message.success("Project updated");
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: aqlConfigKey(editTarget!.id) });
      qc.invalidateQueries({ queryKey: projectHistoryKey(editTarget!.id) });
      setEditOpen(false);
      setEditTarget(null);
      editForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to update project");
    },
  });

  const [aqlLoading, setAqlLoading] = useState(false);
  const openEdit = async (project: Project) => {
    setEditTarget(project);
    setEditOpen(true);
    editForm.setFieldsValue({
      name: project.name,
      description: project.description,
      proposed_end_date: project.proposed_end_date ? dayjs(project.proposed_end_date) : null,
    });
    setAqlLoading(true);
    try {
      const cfg = await qc.fetchQuery({
        queryKey: aqlConfigKey(project.id),
        queryFn: () => getAqlConfig(project.id),
      });
      editForm.setFieldsValue({
        critical_aql: cfg.critical_aql ?? ZERO_TOLERANCE,
        major_aql: cfg.major_aql,
        minor_aql: cfg.minor_aql,
      });
    } catch {
      message.error("Could not load the project's AQLs");
    } finally {
      setAqlLoading(false);
    }
  };

  const q = search.toLowerCase();
  const filtered = projects.filter((p) =>
    p.name.toLowerCase().includes(q) ||
    (p.description ?? "").toLowerCase().includes(q) ||
    (orgById[p.customer_org_id]?.name ?? "").toLowerCase().includes(q)
  );

  const columns: ColumnsType<Project> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Customer Organisation",
      key: "customer_org",
      render: (_: unknown, project: Project) => {
        const org = orgById[project.customer_org_id];
        return org ? org.name : "—";
      },
    },
    {
      title: "Description",
      dataIndex: "description",
      key: "description",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "Proposed End",
      dataIndex: "proposed_end_date",
      key: "proposed_end_date",
      render: (v: string | null) => v ?? "—",
      width: 130,
    },
    {
      title: "S3",
      dataIndex: "s3_bucket_status",
      key: "s3_bucket_status",
      width: 100,
      render: (v: string) => <StatusDot filled={v === "ready"} label={STATUS_LABEL[v] ?? v} />,
    },
    {
      title: "",
      key: "actions",
      width: 250,
      render: (_: unknown, project: Project) => (
        <Space>
          {onOpen && (
            <Button
              size="small"
              type="primary"
              icon={<FolderOpen size={14} />}
              onClick={() => onOpen(project.id)}
            >
              Open
            </Button>
          )}
          <Button
            size="small"
            icon={<Pencil size={14} />}
            onClick={() => openEdit(project)}
          >
            Edit
          </Button>
          <Button
            size="small"
            icon={<History size={14} />}
            onClick={() => setHistoryTarget(project)}
          >
            History
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space wrap style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Projects</span>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>
          New Project
        </Button>
      </Space>

      <Input.Search
        placeholder="Search by name, description or customer org…"
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 16, maxWidth: 420 }}
      />

      <Table dataSource={filtered} columns={columns} rowKey="id" loading={isLoading} />

      {/* Create project modal */}
      <Modal
        title="Create Project"
        open={createOpen}
        onOk={() => createForm.submit()}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={create.mutate}
          initialValues={CREATE_DEFAULTS}
          style={{ marginTop: 12 }}
        >
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="customer_org_id" label="Customer Organisation" rules={[{ required: true }]}>
            <Select
              options={custOrgs.map((o) => ({ value: o.id, label: o.name }))}
              placeholder="Select customer org"
            />
          </Form.Item>
          <Form.Item name="proposed_end_date" label="Proposed End Date">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <AqlFields />
        </Form>
      </Modal>

      {/* Edit project modal */}
      <Modal
        title={`Edit — ${editTarget?.name}`}
        open={editOpen}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditOpen(false); setEditTarget(null); editForm.resetFields(); }}
        confirmLoading={edit.isPending}
        okButtonProps={{ disabled: aqlLoading }}
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={edit.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="proposed_end_date" label="Proposed End Date">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <AqlFields />
          <Alert
            type="info"
            showIcon
            message="New AQLs apply to inspections and customer samples started from now on. Earlier ones keep the AQLs they were started with, and the change is recorded in the project's history."
          />
        </Form>
      </Modal>

      <Drawer
        title={historyTarget ? `History — ${historyTarget.name}` : "History"}
        open={!!historyTarget}
        onClose={() => setHistoryTarget(null)}
        width={520}
        destroyOnClose
      >
        {historyTarget && (
          <ProjectHistory projectId={historyTarget.id} orgName={(id) => orgById[id]?.name} />
        )}
      </Drawer>
    </>
  );
}
