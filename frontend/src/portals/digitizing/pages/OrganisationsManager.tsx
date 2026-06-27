import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Table, Button, Modal, Form, Input, Select, Tag, Space, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Organization } from "@shared/types";

const TYPE_OPTIONS = [
  { value: "customer", label: "Customer" },
];

const TYPE_COLOR: Record<string, string> = {
  digitizing_entity: "blue",
  customer: "green",
};

export default function OrganisationsManager() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const qc = useQueryClient();

  const { data: orgs = [], isLoading } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: () => api.get("/organizations").then((r) => r.data),
  });

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/organizations", values).then((r) => r.data),
    onSuccess: () => {
      message.success("Organisation created");
      qc.invalidateQueries({ queryKey: ["organizations"] });
      setOpen(false);
      form.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create organisation");
    },
  });

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
    { title: "ID", dataIndex: "id", key: "id", width: 60 },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Organisations</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          New Organisation
        </Button>
      </Space>

      <Table dataSource={orgs} columns={columns} rowKey="id" loading={isLoading} />

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
          <Form.Item name="type" label="Type" rules={[{ required: true }]}>
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
