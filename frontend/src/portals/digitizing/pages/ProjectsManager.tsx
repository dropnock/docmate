import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, InputNumber,
  DatePicker, Select, message, Space,
} from "antd";
import { Plus, FolderOpen, Pencil } from "lucide-react";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import api from "@shared/api/client";
import { useProjects } from "@shared/hooks/useProjects";
import StatusDot from "@shared/components/StatusDot";
import type { Project, Organization } from "@shared/types";

const STATUS_LABEL: Record<string, string> = {
  ready: "Ready",
  provisioning: "Provisioning",
  error: "Error",
};

interface Props {
  onOpen?: (projectId: number) => void;
}

export default function ProjectsManager({ onOpen }: Props) {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Project | null>(null);
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
      api.post("/projects", {
        ...values,
        proposed_end_date:
          (values.proposed_end_date as { format?: (s: string) => string } | null)
            ?.format?.("YYYY-MM-DD") ?? null,
      }).then((r) => r.data),
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
      api.patch(`/projects/${editTarget!.id}`, {
        ...values,
        proposed_end_date:
          (values.proposed_end_date as { format?: (s: string) => string } | null)
            ?.format?.("YYYY-MM-DD") ?? null,
      }).then((r) => r.data),
    onSuccess: () => {
      message.success("Project updated");
      qc.invalidateQueries({ queryKey: ["projects"] });
      setEditOpen(false);
      setEditTarget(null);
      editForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to update project");
    },
  });

  const openEdit = (project: Project) => {
    setEditTarget(project);
    editForm.setFieldsValue({
      name: project.name,
      description: project.description,
      proposed_end_date: project.proposed_end_date ? dayjs(project.proposed_end_date) : null,
      stale_threshold_hours: project.stale_threshold_hours,
    });
    setEditOpen(true);
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
      width: 160,
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
        <Form form={createForm} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
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
          <Form.Item name="stale_threshold_hours" label="Stale Threshold (hours)" initialValue={8}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit project modal */}
      <Modal
        title={`Edit — ${editTarget?.name}`}
        open={editOpen}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditOpen(false); setEditTarget(null); editForm.resetFields(); }}
        confirmLoading={edit.isPending}
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
          <Form.Item name="stale_threshold_hours" label="Stale Threshold (hours)">
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
