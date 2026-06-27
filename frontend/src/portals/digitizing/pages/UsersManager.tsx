import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, Select,
  Tag, message, Badge, Space, Popconfirm,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Organization, UserRecord } from "@shared/types";

const ROLES = [
  { value: "admin", label: "Admin" },
  { value: "de_supervisor", label: "DE Supervisor" },
  { value: "de_indexer", label: "DE Indexer" },
  { value: "de_qa_agent", label: "DE QA Agent" },
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
  de_indexer: "cyan",
  de_qa_agent: "geekblue",
  customer_supervisor: "purple",
  customer_qc_agent: "magenta",
};

export default function UsersManager() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
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
      setOpen(false);
      form.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create user");
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

  const columns: ColumnsType<UserRecord> = [
    { title: "Name", dataIndex: "full_name", key: "full_name" },
    { title: "Email", dataIndex: "email", key: "email" },
    {
      title: "Role",
      dataIndex: "role",
      key: "role",
      render: (v: string) => <Tag color={ROLE_COLOR[v] ?? "default"}>{v}</Tag>,
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
      render: (_: unknown, record: UserRecord) =>
        record.is_active ? (
          <Popconfirm
            title="Deactivate this user?"
            onConfirm={() => toggleActive.mutate({ id: record.id, is_active: false })}
            okText="Deactivate"
            okType="danger"
          >
            <Button size="small" danger>
              Deactivate
            </Button>
          </Popconfirm>
        ) : (
          <Button
            size="small"
            onClick={() => toggleActive.mutate({ id: record.id, is_active: true })}
          >
            Reactivate
          </Button>
        ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Users</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          New User
        </Button>
      </Space>

      <Table dataSource={users} columns={columns} rowKey="id" loading={isLoading} />

      <Modal
        title="Create User"
        open={open}
        onOk={() => form.submit()}
        onCancel={() => { setOpen(false); form.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
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
            extra="User will be prompted to change this and set up MFA on first login."
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
    </>
  );
}
