import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card, Table, Button, Modal, Form, Input, Select,
  Tag, message, Badge, Space, Popconfirm,
} from "antd";
import { PlusOutlined, EditOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import PageHeader from "@shared/components/PageHeader";
import type { Organization, UserRecord } from "@shared/types";

const ROLES = [
  { value: "admin", label: "Admin" },
  { value: "de_supervisor", label: "DE Supervisor" },
  { value: "de_staff", label: "DE Staff" },
  { value: "customer_supervisor", label: "Customer Supervisor" },
  { value: "customer_qc_agent", label: "Customer QC Agent" },
];

const PORTALS = [
  { value: "digitizing", label: "Digitizing" },
  { value: "customer", label: "Customer" },
];

const ROLE_COLOR: Record<string, string> = {
  admin: "red",
  de_supervisor: "blue",
  de_staff: "cyan",
  customer_supervisor: "purple",
  customer_qc_agent: "magenta",
};

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string; loc?: (string | number)[] };
    const loc = first.loc ?? [];
    const field = loc[loc.length - 1];
    return field ? `${field}: ${first.msg ?? "invalid value"}` : (first.msg ?? "Request failed");
  }
  return "Request failed";
}

export default function UsersManager() {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<UserRecord | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: users = [], isLoading } = useQuery<UserRecord[]>({
    queryKey: ["users"],
    queryFn: () => api.get("/users").then((r) => r.data),
  });

  const { data: orgs = [] } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
  });

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/users", values).then((r) => r.data),
    onSuccess: () => {
      message.success("User created");
      qc.invalidateQueries({ queryKey: ["users"] });
      setCreateOpen(false);
      createForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: unknown } } };
      message.error(formatDetail(err.response?.data?.detail) ?? "Failed to create user");
    },
  });

  const update = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.patch(`/users/${editTarget!.id}`, values).then((r) => r.data),
    onSuccess: () => {
      message.success("User updated");
      qc.invalidateQueries({ queryKey: ["users"] });
      setEditOpen(false);
      setEditTarget(null);
      editForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: unknown } } };
      message.error(formatDetail(err.response?.data?.detail) ?? "Failed to update user");
    },
  });

  const toggleActive = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      api.patch(`/users/${id}`, { is_active }).then((r) => r.data),
    onSuccess: (_data, vars) => {
      message.success(vars.is_active ? "User activated" : "User deactivated");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: () => message.error("Failed to update user"),
  });

  const openEdit = (user: UserRecord) => {
    setEditTarget(user);
    editForm.setFieldsValue({
      full_name: user.full_name,
      role: user.role,
      organization_id: user.organization_id ?? undefined,
    });
    setEditOpen(true);
  };

  const q = search.toLowerCase();
  const filteredUsers = users.filter((u) =>
    u.full_name.toLowerCase().includes(q) ||
    u.email.toLowerCase().includes(q) ||
    u.role.toLowerCase().includes(q) ||
    u.portal.toLowerCase().includes(q)
  );

  const columns: ColumnsType<UserRecord> = [
    { title: "Name", dataIndex: "full_name", key: "full_name" },
    { title: "Email", dataIndex: "email", key: "email" },
    {
      title: "Role",
      dataIndex: "role",
      key: "role",
      render: (v: string) => <Tag color={ROLE_COLOR[v] ?? "default"}>{v.replace(/_/g, " ")}</Tag>,
    },
    { title: "Portal", dataIndex: "portal", key: "portal" },
    {
      title: "Status",
      dataIndex: "is_active",
      key: "is_active",
      render: (v: boolean) =>
        v ? (
          <Badge status="success" text="Active" />
        ) : (
          <Badge status="default" text="Inactive" />
        ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 180,
      render: (_: unknown, record: UserRecord) => (
        <Space>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            Edit
          </Button>
          {record.is_active ? (
            <Popconfirm
              title="Deactivate this user?"
              onConfirm={() => toggleActive.mutate({ id: record.id, is_active: false })}
              okText="Deactivate"
              okType="danger"
            >
              <Button size="small" danger>Deactivate</Button>
            </Popconfirm>
          ) : (
            <Button
              size="small"
              onClick={() => toggleActive.mutate({ id: record.id, is_active: true })}
            >
              Reactivate
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Users"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            New User
          </Button>
        }
      />

      <Card>
        <Input.Search
          placeholder="Search by name, email, role or portal…"
          allowClear
          onChange={(e) => setSearch(e.target.value)}
          style={{ marginBottom: 16, maxWidth: 420 }}
        />

        <Table dataSource={filteredUsers} columns={columns} rowKey="id" loading={isLoading} scroll={{ x: "max-content" }} />
      </Card>

      {/* Create user modal */}
      <Modal
        title="Create User"
        open={createOpen}
        onOk={() => createForm.submit()}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="full_name" label="Full Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="email"
            label="Email"
            rules={[{ required: true, type: "email" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="temp_password"
            label="Temporary Password"
            rules={[{ required: true, min: 6 }]}
            extra="User will be prompted to change this on first login."
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select options={ROLES} />
          </Form.Item>
          <Form.Item name="portal" label="Portal" rules={[{ required: true }]}>
            <Select options={PORTALS} />
          </Form.Item>
          <Form.Item name="organization_id" label="Organisation">
            <Select
              allowClear
              placeholder="None"
              options={orgs.map((o) => ({
                value: o.id,
                label: `${o.name} (${o.type})`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit user modal */}
      <Modal
        title={`Edit — ${editTarget?.full_name}`}
        open={editOpen}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditOpen(false); setEditTarget(null); editForm.resetFields(); }}
        confirmLoading={update.isPending}
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={update.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="full_name" label="Full Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select options={ROLES} />
          </Form.Item>
          <Form.Item name="organization_id" label="Organisation">
            <Select
              allowClear
              placeholder="None"
              options={orgs.map((o) => ({
                value: o.id,
                label: `${o.name} (${o.type})`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
