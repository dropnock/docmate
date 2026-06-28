import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Table, Button, Modal, Form, Input, Select, Tag, Space, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Shift, Project } from "@shared/types";

const TIMEZONES = [
  "UTC", "America/New_York", "America/Chicago", "America/Los_Angeles",
  "America/Toronto", "Europe/London", "Europe/Paris", "Europe/Berlin",
  "Asia/Dubai", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney",
];

const TIMES = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`);

export default function ShiftsManager() {
  const [open, setOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [form] = Form.useForm();
  const [assignForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: shifts = [], isLoading } = useQuery<Shift[]>({
    queryKey: ["shifts"],
    queryFn: () => api.get("/shifts").then((r) => r.data),
  });

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/shifts", values).then((r) => r.data),
    onSuccess: () => {
      message.success("Shift created");
      qc.invalidateQueries({ queryKey: ["shifts"] });
      setOpen(false);
      form.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create shift");
    },
  });

  const assignToProject = useMutation({
    mutationFn: (values: { project_id: number; shift_id: number }) =>
      api.post(`/projects/${values.project_id}/shifts`, { shift_id: values.shift_id }).then((r) => r.data),
    onSuccess: () => {
      message.success("Shift assigned to project");
      setAssignOpen(false);
      assignForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to assign shift");
    },
  });

  const columns: ColumnsType<Shift> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Hours",
      key: "hours",
      render: (_: unknown, s: Shift) => `${s.start_time} – ${s.end_time}`,
    },
    { title: "Timezone", dataIndex: "timezone", key: "timezone" },
    { title: "ID", dataIndex: "id", width: 60 },
    {
      title: "",
      key: "actions",
      width: 130,
      render: (_: unknown, s: Shift) => (
        <Button
          size="small"
          onClick={() => {
            assignForm.setFieldValue("shift_id", s.id);
            setAssignOpen(true);
          }}
        >
          Assign to Project
        </Button>
      ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Shifts</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          New Shift
        </Button>
      </Space>

      <Table dataSource={shifts} columns={columns} rowKey="id" loading={isLoading} />

      {/* Create shift modal */}
      <Modal
        title="Create Shift"
        open={open}
        onOk={() => form.submit()}
        onCancel={() => { setOpen(false); form.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="name" label="Shift Name" rules={[{ required: true }]}>
            <Input placeholder="e.g. Morning, Afternoon, Night" />
          </Form.Item>
          <Form.Item
            name="start_time"
            label="Start Time"
            rules={[{ required: true }]}
          >
            <Select options={TIMES.map((t) => ({ value: t, label: t }))} showSearch />
          </Form.Item>
          <Form.Item
            name="end_time"
            label="End Time"
            rules={[{ required: true }]}
          >
            <Select options={TIMES.map((t) => ({ value: t, label: t }))} showSearch />
          </Form.Item>
          <Form.Item name="timezone" label="Timezone" rules={[{ required: true }]} initialValue="UTC">
            <Select options={TIMEZONES.map((tz) => ({ value: tz, label: tz }))} showSearch />
          </Form.Item>
        </Form>
      </Modal>

      {/* Assign to project modal */}
      <Modal
        title="Assign Shift to Project"
        open={assignOpen}
        onOk={() => assignForm.submit()}
        onCancel={() => { setAssignOpen(false); assignForm.resetFields(); }}
        confirmLoading={assignToProject.isPending}
        destroyOnClose
      >
        <Form form={assignForm} layout="vertical" onFinish={assignToProject.mutate} style={{ marginTop: 12 }}>
          <Form.Item name="shift_id" label="Shift" rules={[{ required: true }]}>
            <Select options={shifts.map((s) => ({ value: s.id, label: `${s.name} (${s.start_time}–${s.end_time})` }))} />
          </Form.Item>
          <Form.Item name="project_id" label="Project" rules={[{ required: true }]}>
            <Select
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
              placeholder="Select project"
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
