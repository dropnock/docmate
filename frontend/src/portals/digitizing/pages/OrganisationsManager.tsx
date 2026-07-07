import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, Select,
  Space, message, Drawer, Typography,
} from "antd";
import { Plus, Database, FolderOpen } from "lucide-react";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import { useProjects } from "@shared/hooks/useProjects";
import StatusDot from "@shared/components/StatusDot";
import type { Organization, Project } from "@shared/types";

const TYPE_OPTIONS = [
  { value: "customer", label: "Customer" },
];

const TYPE_LABEL: Record<string, string> = {
  digitizing_entity: "Digitizing Entity",
  customer: "Customer",
};

const PROJECT_STATUS_LABEL: Record<string, string> = {
  ready: "Ready",
  provisioning: "Provisioning",
  error: "Error",
};

/** Type badge — filled primary for the internal entity, outlined primary for
 * customers. No other colors, per design spec. */
function TypeBadge({ type }: { type: string }) {
  const isInternal = type === "digitizing_entity";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 500,
        ...(isInternal
          ? { background: "#1E40AF", color: "#FFFFFF" }
          : { background: "#FFFFFF", color: "#1E40AF", border: "1px solid #1E40AF" }),
      }}
    >
      {TYPE_LABEL[type] ?? type}
    </span>
  );
}

function BucketStatusDot({ status }: { status: string | null }) {
  if (!status) return <span style={{ color: "#64748B" }}>—</span>;
  return <StatusDot filled={status === "ready"} label={PROJECT_STATUS_LABEL[status] ?? status} />;
}

function CodeChip({ children }: { children: string }) {
  return (
    <span
      className="docmate-code-chip"
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 6,
        fontSize: 12,
        color: "#0F172A",
        background: "#F8FAFC",
        border: "1px solid #E2E8F0",
      }}
    >
      {children}
    </span>
  );
}

export default function OrganisationsManager() {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState<Organization | null>(null);
  const [form] = Form.useForm();
  const qc = useQueryClient();

  const { data: orgs = [], isLoading } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
    refetchInterval: (query) =>
      query.state.data?.some((o) => o.s3_bucket_status === "provisioning") ? 5000 : false,
  });

  const { data: allProjects = [] } = useProjects();

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
      width: 120,
      render: (v: string) => <BucketStatusDot status={v} />,
    },
  ];

  const columns: ColumnsType<Organization> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Type",
      dataIndex: "type",
      key: "type",
      render: (v: string) => <TypeBadge type={v} />,
    },
    {
      title: "S3 Bucket",
      key: "bucket",
      render: (_: unknown, org: Organization) => {
        if (!org.s3_bucket_name) return <span style={{ color: "#64748B" }}>—</span>;
        return (
          <Space size={8}>
            <Database size={14} color="#64748B" />
            <CodeChip>{org.s3_bucket_name}</CodeChip>
            {org.s3_bucket_status && <BucketStatusDot status={org.s3_bucket_status} />}
          </Space>
        );
      },
    },
    {
      title: "Realm",
      dataIndex: "realm_slug",
      key: "realm_slug",
      render: (v: string | null) =>
        v ? <CodeChip>{v}</CodeChip> : <span style={{ color: "#64748B" }}>—</span>,
    },
    {
      title: "",
      key: "actions",
      width: 130,
      render: (_: unknown, org: Organization) => (
        <Button
          size="small"
          icon={<FolderOpen size={14} />}
          onClick={(e) => { e.stopPropagation(); setSelectedOrg(org); }}
        >
          Projects
        </Button>
      ),
    },
  ];

  return (
    <>
      <Space wrap style={{ marginBottom: 32, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 28, fontWeight: 700, color: "#0F172A" }}>Organisations</span>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => setOpen(true)}>
          New Organisation
        </Button>
      </Space>

      <Input.Search
        placeholder="Search by name or type…"
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 32, maxWidth: 360 }}
      />

      <div className="docmate-table-view">
        <Table
          dataSource={filteredOrgs}
          columns={columns}
          rowKey="id"
          loading={isLoading}
          onRow={(org) => ({ onClick: () => setSelectedOrg(org), style: { cursor: "pointer" } })}
          style={{ borderRadius: 12, overflow: "hidden" }}
        />
      </div>

      {/* Stacked cards below 768px — no horizontal scrolling */}
      <div className="docmate-card-view">
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {filteredOrgs.map((org) => (
            <div
              key={org.id}
              onClick={() => setSelectedOrg(org)}
              style={{
                border: "1px solid #E2E8F0",
                borderRadius: 12,
                padding: 20,
                background: "#FFFFFF",
                cursor: "pointer",
              }}
            >
              <Space wrap style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
                <span style={{ fontWeight: 600, color: "#0F172A" }}>{org.name}</span>
                <TypeBadge type={org.type} />
              </Space>

              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 12, color: "#64748B", marginBottom: 4 }}>S3 Bucket</div>
                {org.s3_bucket_name ? (
                  <Space size={8}>
                    <CodeChip>{org.s3_bucket_name}</CodeChip>
                    {org.s3_bucket_status && <BucketStatusDot status={org.s3_bucket_status} />}
                  </Space>
                ) : (
                  <span style={{ color: "#64748B" }}>—</span>
                )}
              </div>

              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, color: "#64748B", marginBottom: 4 }}>Realm</div>
                {org.realm_slug ? <CodeChip>{org.realm_slug}</CodeChip> : <span style={{ color: "#64748B" }}>—</span>}
              </div>

              <Button
                block
                size="small"
                icon={<FolderOpen size={14} />}
                onClick={(e) => { e.stopPropagation(); setSelectedOrg(org); }}
              >
                Projects
              </Button>
            </div>
          ))}
        </Space>
      </div>

      {/* Projects drawer */}
      <Drawer
        title={
          <Space>
            <FolderOpen size={16} />
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
        <div style={{ color: "#64748B", fontSize: 12, marginTop: 8 }}>
          Creating a customer organisation will automatically provision a Keycloak realm and an S3 bucket.
        </div>
      </Modal>
    </>
  );
}
