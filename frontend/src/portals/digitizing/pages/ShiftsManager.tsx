import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Input, Select,
  Space, Tag, message, Popconfirm,
} from "antd";
import { Plus, Pencil, Trash2, Unlink } from "lucide-react";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import { useProjects } from "@shared/hooks/useProjects";
import type { Shift, ShiftProjectAssignment } from "@shared/types";

const TIMEZONES = [
  { value: "America/Jamaica",       label: "Jamaica (UTC−5, no DST)" },
  { value: "America/Barbados",      label: "Barbados (UTC−4, no DST)" },
  { value: "America/Port_of_Spain", label: "Trinidad & Tobago (UTC−4, no DST)" },
];

const TIMES = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`);

export default function ShiftsManager() {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Shift | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const [assignForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: shifts = [], isLoading } = useQuery<Shift[]>({
    queryKey: ["shifts"],
    queryFn: () => api.get("/shifts").then((r) => r.data),
  });

  const { data: projects = [] } = useProjects();

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/shifts", values).then((r) => r.data),
    onSuccess: () => {
      message.success("Shift created");
      qc.invalidateQueries({ queryKey: ["shifts"] });
      setCreateOpen(false);
      createForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create shift");
    },
  });

  const update = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.patch(`/shifts/${editTarget!.id}`, values).then((r) => r.data),
    onSuccess: () => {
      message.success("Shift updated");
      qc.invalidateQueries({ queryKey: ["shifts"] });
      setEditOpen(false);
      setEditTarget(null);
      editForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to update shift");
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/shifts/${id}`),
    onSuccess: () => {
      message.success("Shift deleted");
      qc.invalidateQueries({ queryKey: ["shifts"] });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to delete shift");
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

  const deassign = useMutation({
    mutationFn: ({ projectId, shiftId }: { projectId: number; shiftId: number }) =>
      api.delete(`/projects/${projectId}/shifts/${shiftId}`),
    onSuccess: () => {
      message.success("Shift removed from project");
      qc.invalidateQueries({ queryKey: ["shifts"] });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to deassign shift");
    },
  });

  const openEdit = (shift: Shift) => {
    setEditTarget(shift);
    editForm.setFieldsValue({
      name: shift.name,
      start_time: shift.start_time.slice(0, 5),
      end_time: shift.end_time.slice(0, 5),
      timezone: shift.timezone,
    });
    setEditOpen(true);
  };

  const q = search.toLowerCase();
  const filteredShifts = shifts.filter((s) =>
    s.name.toLowerCase().includes(q) ||
    s.timezone.toLowerCase().includes(q)
  );

  const columns: ColumnsType<Shift> = [
    { title: "Name", dataIndex: "name", key: "name" },
    {
      title: "Hours",
      key: "hours",
      render: (_: unknown, s: Shift) => `${s.start_time.slice(0, 5)} – ${s.end_time.slice(0, 5)}`,
    },
    { title: "Timezone", dataIndex: "timezone", key: "timezone" },
    {
      title: "Project",
      key: "project",
      render: (_: unknown, s: Shift) =>
        s.project_assignments.length === 0 ? (
          <Tag color="default">Unassigned</Tag>
        ) : (
          <Space wrap>
            {s.project_assignments.map((pa: ShiftProjectAssignment) => (
              <Space key={pa.project_shift_id} size={4}>
                <Tag>{pa.project_name}</Tag>
                <Popconfirm
                  title={`Remove from "${pa.project_name}"?`}
                  onConfirm={() => deassign.mutate({ projectId: pa.project_id, shiftId: s.id })}
                  okText="Remove"
                  okType="danger"
                >
                  <Button
                    size="small"
                    danger
                    icon={<Unlink size={14} />}
                    title="Deassign from project"
                  />
                </Popconfirm>
              </Space>
            ))}
          </Space>
        ),
    },
    {
      title: "",
      key: "actions",
      width: 220,
      render: (_: unknown, s: Shift) => (
        <Space>
          <Button
            size="small"
            onClick={() => {
              assignForm.setFieldValue("shift_id", s.id);
              setAssignOpen(true);
            }}
          >
            Assign to Project
          </Button>
          <Button
            size="small"
            icon={<Pencil size={14} />}
            onClick={() => openEdit(s)}
          />
          <Popconfirm
            title="Delete this shift?"
            description="This will also remove all project assignments for this shift."
            onConfirm={() => remove.mutate(s.id)}
            okText="Delete"
            okType="danger"
          >
            <Button size="small" danger icon={<Trash2 size={14} />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const shiftFormFields = (
    <>
      <Form.Item name="name" label="Shift Name" rules={[{ required: true }]}>
        <Input placeholder="e.g. Morning, Afternoon, Night" />
      </Form.Item>
      <Form.Item name="start_time" label="Start Time" rules={[{ required: true }]}>
        <Select options={TIMES.map((t) => ({ value: t, label: t }))} showSearch />
      </Form.Item>
      <Form.Item name="end_time" label="End Time" rules={[{ required: true }]}>
        <Select options={TIMES.map((t) => ({ value: t, label: t }))} showSearch />
      </Form.Item>
      <Form.Item name="timezone" label="Timezone" rules={[{ required: true }]} initialValue="America/Jamaica">
        <Select options={TIMEZONES} />
      </Form.Item>
    </>
  );

  return (
    <>
      <Space wrap style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Shifts</span>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>
          New Shift
        </Button>
      </Space>

      <Input.Search
        placeholder="Search by name or timezone…"
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 16, maxWidth: 360 }}
      />

      <Table dataSource={filteredShifts} columns={columns} rowKey="id" loading={isLoading} />

      {/* Create shift modal */}
      <Modal
        title="Create Shift"
        open={createOpen}
        onOk={() => createForm.submit()}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" onFinish={create.mutate} style={{ marginTop: 12 }}>
          {shiftFormFields}
        </Form>
      </Modal>

      {/* Edit shift modal */}
      <Modal
        title={`Edit — ${editTarget?.name}`}
        open={editOpen}
        onOk={() => editForm.submit()}
        onCancel={() => { setEditOpen(false); setEditTarget(null); editForm.resetFields(); }}
        confirmLoading={update.isPending}
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={update.mutate} style={{ marginTop: 12 }}>
          {shiftFormFields}
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
            <Select options={shifts.map((s) => ({ value: s.id, label: `${s.name} (${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)})` }))} />
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
