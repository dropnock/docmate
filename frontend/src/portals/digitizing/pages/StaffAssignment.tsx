import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Select,
  Tag, message, Space, Empty, Divider,
} from "antd";
import { UserAddOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Project, Shift, AvailableStaff, UserRecord } from "@shared/types";

export default function StaffAssignment() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedShiftId, setSelectedShiftId] = useState<number | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [shiftOpen, setShiftOpen] = useState(false);
  const [assignForm] = Form.useForm();
  const [shiftForm] = Form.useForm();
  const qc = useQueryClient();

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });

  const { data: shifts = [] } = useQuery<Shift[]>({
    queryKey: ["shifts"],
    queryFn: () => api.get("/shifts").then((r) => r.data),
  });

  const { data: users = [] } = useQuery<UserRecord[]>({
    queryKey: ["users"],
    queryFn: () => api.get("/users").then((r) => r.data),
  });

  const { data: assignedStaff = [], isLoading: staffLoading } = useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", selectedProjectId, selectedShiftId],
    queryFn: () =>
      api
        .get(`/projects/${selectedProjectId}/available-staff?shift_id=${selectedShiftId}`)
        .then((r) => r.data),
    enabled: !!selectedProjectId && !!selectedShiftId,
  });

  const assignStaff = useMutation({
    mutationFn: (values: { user_id: number; shift_id: number }) =>
      api.post(`/projects/${selectedProjectId}/staff`, values).then((r) => r.data),
    onSuccess: () => {
      message.success("Staff assigned to project");
      qc.invalidateQueries({ queryKey: ["available-staff"] });
      setAssignOpen(false);
      assignForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to assign staff");
    },
  });

  const createShift = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post("/shifts", values).then((r) => r.data),
    onSuccess: () => {
      message.success("Shift created");
      qc.invalidateQueries({ queryKey: ["shifts"] });
      setShiftOpen(false);
      shiftForm.resetFields();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create shift");
    },
  });

  const staffColumns: ColumnsType<AvailableStaff> = [
    { title: "Name", dataIndex: "full_name", key: "full_name" },
    { title: "Email", dataIndex: "email", key: "email" },
    {
      title: "Role",
      dataIndex: "role",
      key: "role",
      render: (v: string) => <Tag>{v}</Tag>,
    },
  ];

  const canViewStaff = !!selectedProjectId && !!selectedShiftId;

  return (
    <>
      <span style={{ fontSize: 20, fontWeight: 600 }}>Staff Assignment</span>

      <Space style={{ marginTop: 16, marginBottom: 16 }} wrap>
        <Select
          placeholder="Select project"
          style={{ width: 240 }}
          options={projects.map((p) => ({ value: p.id, label: p.name }))}
          onChange={(v) => { setSelectedProjectId(v); setSelectedShiftId(null); }}
          value={selectedProjectId}
        />
        <Select
          placeholder="Select shift"
          style={{ width: 200 }}
          options={shifts.map((s) => ({
            value: s.id,
            label: `${s.name} (${s.start_time}–${s.end_time})`,
          }))}
          onChange={setSelectedShiftId}
          value={selectedShiftId}
          disabled={!selectedProjectId}
          dropdownRender={(menu) => (
            <>
              {menu}
              <Divider style={{ margin: "4px 0" }} />
              <Button
                type="link"
                icon={<PlusOutlined />}
                style={{ padding: "4px 8px" }}
                onClick={() => setShiftOpen(true)}
              >
                Create new shift
              </Button>
            </>
          )}
        />
        {canViewStaff && (
          <Button
            type="primary"
            icon={<UserAddOutlined />}
            onClick={() => {
              assignForm.setFieldValue("shift_id", selectedShiftId);
              setAssignOpen(true);
            }}
          >
            Add Staff
          </Button>
        )}
      </Space>

      {canViewStaff ? (
        <Table
          dataSource={assignedStaff}
          columns={staffColumns}
          rowKey="id"
          loading={staffLoading}
          locale={{ emptyText: "No staff assigned to this project / shift yet" }}
        />
      ) : (
        <Empty description="Select a project and shift to view assigned staff" />
      )}

      {/* Assign staff modal */}
      <Modal
        title="Add Staff to Project"
        open={assignOpen}
        onOk={() => assignForm.submit()}
        onCancel={() => { setAssignOpen(false); assignForm.resetFields(); }}
        confirmLoading={assignStaff.isPending}
        destroyOnClose
      >
        <Form
          form={assignForm}
          layout="vertical"
          onFinish={assignStaff.mutate}
          style={{ marginTop: 12 }}
        >
          <Form.Item name="user_id" label="User" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="Search users"
              options={users
                .filter((u) => u.is_active)
                .map((u) => ({
                  value: u.id,
                  label: `${u.full_name} — ${u.role} (${u.email})`,
                }))}
            />
          </Form.Item>
          <Form.Item name="shift_id" label="Shift" rules={[{ required: true }]}>
            <Select
              options={shifts.map((s) => ({ value: s.id, label: s.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Create shift modal */}
      <Modal
        title="Create Shift"
        open={shiftOpen}
        onOk={() => shiftForm.submit()}
        onCancel={() => { setShiftOpen(false); shiftForm.resetFields(); }}
        confirmLoading={createShift.isPending}
        destroyOnClose
      >
        <Form
          form={shiftForm}
          layout="vertical"
          onFinish={createShift.mutate}
          style={{ marginTop: 12 }}
          initialValues={{ timezone: "UTC" }}
        >
          <Form.Item name="name" label="Shift Name" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "Morning", label: "Morning" },
                { value: "Afternoon", label: "Afternoon" },
                { value: "Night", label: "Night" },
              ]}
              mode={undefined}
              allowClear={false}
              showSearch
              // allow free-form input via the search box
              filterOption={false}
              onSearch={(v) => shiftForm.setFieldValue("name", v)}
            />
          </Form.Item>
          <Form.Item
            name="start_time"
            label="Start Time (HH:MM)"
            rules={[{ required: true, pattern: /^\d{2}:\d{2}$/, message: "Use HH:MM format" }]}
          >
            <Select
              options={["06:00","07:00","08:00","09:00","14:00","22:00"].map((t) => ({
                value: t, label: t,
              }))}
              showSearch
              filterOption={false}
              onSearch={(v) => shiftForm.setFieldValue("start_time", v)}
              allowClear={false}
            />
          </Form.Item>
          <Form.Item
            name="end_time"
            label="End Time (HH:MM)"
            rules={[{ required: true, pattern: /^\d{2}:\d{2}$/, message: "Use HH:MM format" }]}
          >
            <Select
              options={["14:00","15:00","16:00","17:00","18:00","22:00","06:00"].map((t) => ({
                value: t, label: t,
              }))}
              showSearch
              filterOption={false}
              onSearch={(v) => shiftForm.setFieldValue("end_time", v)}
              allowClear={false}
            />
          </Form.Item>
          <Form.Item name="timezone" label="Timezone" rules={[{ required: true }]}>
            <Select
              options={["UTC","America/New_York","America/Chicago","America/Los_Angeles","Europe/London","Europe/Paris","Asia/Tokyo"].map(
                (tz) => ({ value: tz, label: tz })
              )}
              showSearch
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
