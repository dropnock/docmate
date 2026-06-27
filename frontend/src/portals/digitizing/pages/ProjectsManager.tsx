import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, InputNumber,
  DatePicker, Select, Tag, message, Space,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Project, Organization } from "@shared/types";

const STATUS_COLOR: Record<string, string> = {
  ready: "green",
  provisioning: "gold",
  error: "red",
};

export default function ProjectsManager() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const qc = useQueryClient();

  const { data: projects = [], isLoading } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });

  const { data: orgs = [] } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
  });

  const custOrgs = orgs.filter((o) => o.type === "customer");

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
      setOpen(false);
      form.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create project");
    },
  });

  const columns: ColumnsType<Project> = [
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
    },
    {
      title: "S3 Status",
      dataIndex: "s3_bucket_status",
      key: "s3_bucket_status",
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? "default"}>{v}</Tag>
      ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Projects</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          New Project
        </Button>
      </Space>

      <Table dataSource={projects} columns={columns} rowKey="id" loading={isLoading} />

      <Modal
        title="Create Project"
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
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="customer_org_id"
            label="Customer Organisation"
            rules={[{ required: true }]}
          >
            <Select
              options={custOrgs.map((o) => ({ value: o.id, label: o.name }))}
              placeholder="Select customer org"
            />
          </Form.Item>
          <Form.Item name="proposed_end_date" label="Proposed End Date">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="stale_threshold_hours"
            label="Stale Threshold (hours)"
            initialValue={8}
          >
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
