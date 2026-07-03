import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, Select,
  Tag, Space, message, Badge, Drawer, Typography,
} from "antd";
import { PlusOutlined, DatabaseOutlined, EditOutlined, FolderOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Organization, Project } from "@shared/types";

const TYPE_OPTIONS = [
  { value: "customer", label: "Customer" },
];

const TYPE_COLOR: Record<string, string> = {
  digitizing_entity: "blue",
  customer: "green",
};

const BUCKET_STATUS_BADGE: Record<string, "success" | "processing" | "error" | "default"> = {
  ready: "success",
  provisioning: "processing",
  error: "error",
};

const PROJECT_STATUS_COLOR: Record<string, string> = {
  ready: "green",
  provisioning: "gold",
  error: "red",
};

export default function OrganisationsManager() {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [editOrg, setEditOrg] = useState<Organization | null>(null);
  const [selectedOrg, setSelectedOrg] = useState<Organization | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: orgs = [], isLoading } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
    refetchInterval: (query) =>
      query.state.data?.some((o) => o.s3_bucket_status === "provisioning") ? 5000 : false,
  });

  const { data: allProjects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });

  const update = useMutation({
    mutationFn: ({ id, values }: { id: number; values: Record<string, unknown> }) =>
      api.patch(`/organizations/${id}`, values).then((r) => r.data),
    onSuccess: () => {
      message.success("Organisation updated");
      qc.invalidateQueries({ queryKey: ["organizations"] });
      setEditOrg(null);
      editForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to update organisation");
    },
  });

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/organizations", values).then((r) => r.data),
    onSuccess: () => {
      message.success("Organisation created — provisioning Keycloak realm and S3 bucket…");
      qc.invalidateQueries({ queryKey: ["organizations"] });
      setOpen(false);
      form.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create organisation");
    },
  });

  const q = search.toLowerCase();
  const filteredOrgs = orgs.filter((o) =>
    o.name.toLowerCase().includes(q) ||
    o.type.toLowerCase().includes(q)
  );

  const orgProjects = selectedOrg
    ? allProjects.filter((p) => p.customer_org_id === selectedOrg.id)
    : [];

  const projectColumns: ColumnsType<Project> = [
    { title: "Name", dataIndex: "name", key: "name" },
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
      render: (v: string) => <Tag color={PROJECT_STATUS_COLOR[v] ?? "default"}>{v}</Tag>,
    },
  ];

  const columns: ColumnsType<Organization> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Type",
      dataIndex: "type",
      key: "type",
      render: (v: string) => (
        <Tag color={TYPE_COLOR[v] ?? "default"}>
          {v === "digitizing_entity" ? "Digitizing Entity" : "Customer"}
        </Tag>
      ),
    },
    {
      title: "S3 Bucket",
      key: "bucket",
      render: (_: unknown, org: Organization) => {
        if (!org.s3_bucket_name) return <Tag>—</Tag>;
        return (
          <Space>
            <DatabaseOutlined style={{ color: "#8c8c8c" }} />
            <span style={{ fontFamily: "monospace", fontSize: 12 }}>{org.s3_bucket_name}</span>
            {org.s3_bucket_status && (
              <Badge
                status={BUCKET_STATUS_BADGE[org.s3_bucket_status] ?? "default"}
                text={org.s3_bucket_status}
              />
            )}
          </Space>
        );
      },
    },
    {
      title: "Realm",
      dataIndex: "realm_slug",
      key: "realm_slug",
      render: (v: string | null) =>
        v ? (
          <Tag color="purple" style={{ fontFamily: "monospace" }}>{v}</Tag>
        ) : (
          <Tag>—</Tag>
        ),
    },
    {
      title: "",
      key: "actions",
      width: 160,
      render: (_: unknown, org: Organization) => (
        <Space>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              setEditOrg(org);
              editForm.setFieldsValue({ name: org.name });
            }}
          >
            Edit
          </Button>
          <Button
            size="small"
            icon={<FolderOutlined />}
            onClick={(e) => { e.stopPropagation(); setSelectedOrg(org); }}
          >
            Projects
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Organisations</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          New Organisation
        </Button>
      </Space>

      <Input.Search
        placeholder="Search by name or type…"
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 16, maxWidth: 360 }}
      />

      <Table
        dataSource={filteredOrgs}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        onRow={(org) => ({ onClick: () => setSelectedOrg(org), style: { cursor: "pointer" } })}
      />

      {/* Projects drawer */}
      <Drawer
        title={
          <Space>
            <FolderOutlined />
            <span>Projects — {selectedOrg?.name}</span>
          </Space>
        }
        open={!!selectedOrg}
        onClose={() => setSelectedOrg(null)}
        width={640}
      >
        {orgProjects.length === 0 ? (
          <Typography.Text type="secondary">No projects assigned to this organisation.</Typography.Text>
        ) : (
          <Table
            dataSource={orgProjects}
            columns={projectColumns}
            rowKey="id"
            size="small"
            pagination={false}
          />
        )}
      </Drawer>

      {/* Edit organisation modal */}
      <Modal
        title={`Edit Organisation — ${editOrg?.name}`}
        open={!!editOrg}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditOrg(null); editForm.resetFields(); }}
        confirmLoading={update.isPending}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(values) => update.mutate({ id: editOrg!.id, values })}
          style={{ marginTop: 12 }}
        >
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="Type">
            <Tag color={TYPE_COLOR[editOrg?.type ?? ""] ?? "default"}>
              {editOrg?.type === "digitizing_entity" ? "Digitizing Entity" : "Customer"}
            </Tag>
            <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
              Type cannot be changed after creation.
            </Typography.Text>
          </Form.Item>
          {editOrg?.realm_slug && (
            <Form.Item label="Keycloak Realm">
              <Tag color="purple" style={{ fontFamily: "monospace" }}>{editOrg.realm_slug}</Tag>
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* Create organisation modal */}
      <Modal
        title="Create Organisation"
        open={open}
        onOk={() => form.submit()}
        onCancel={() => { setOpen(false); form.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="type" label="Type" rules={[{ required: true }]} initialValue="customer">
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
        </Form>
        <div style={{ color: "#8c8c8c", fontSize: 12, marginTop: 8 }}>
          Creating a customer organisation will automatically provision a Keycloak realm and an S3 bucket.
        </div>
      </Modal>
    </>
  );
}
