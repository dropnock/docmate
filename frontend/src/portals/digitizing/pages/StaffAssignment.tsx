import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table, Button, Modal, Form, Select,
  Tag, message, Space, Empty,
} from "antd";
import { UserAddOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@shared/api/client";
import type { Project, Shift, AvailableStaff, UserRecord } from "@shared/types";

const ROLE_COLOR: Record<string, string> = {
  de_indexer: "blue",
  de_qa_agent: "gold",
  de_supervisor: "green",
  customer_supervisor: "purple",
  customer_qc_agent: "orange",
  admin: "red",
};

export default function StaffAssignment() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedShiftId, setSelectedShiftId] = useState<number | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignForm] = Form.useForm();
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

  const staffColumns: ColumnsType<AvailableStaff> = [
    { title: "Name", dataIndex: "full_name", key: "full_name" },
    { title: "Email", dataIndex: "email", key: "email" },
    {
      title: "Role",
      dataIndex: "role",
      key: "role",
      render: (v: string) => (
        <Tag color={ROLE_COLOR[v] ?? "default"}>{v.replace(/_/g, " ")}</Tag>
      ),
    },
  ];

  const canViewStaff = !!selectedProjectId && !!selectedShiftId;

  const projectShifts = selectedProjectId
    ? shifts  // show all shifts; backend validates shift belongs to project
    : [];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Staff Assignment</span>
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

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="Select project"
          style={{ width: 240 }}
          options={projects.map((p) => ({ value: p.id, label: p.name }))}
          onChange={(v) => { setSelectedProjectId(v); setSelectedShiftId(null); }}
          value={selectedProjectId}
        />
        <Select
          placeholder="Select shift"
          style={{ width: 220 }}
          options={projectShifts.map((s) => ({
            value: s.id,
            label: `${s.name} (${s.start_time}–${s.end_time})`,
          }))}
          onChange={setSelectedShiftId}
          value={selectedShiftId}
          disabled={!selectedProjectId}
        />
      </Space>

      {canViewStaff ? (
        <Table
          dataSource={assignedStaff}
          columns={staffColumns}
          rowKey="id"
          loading={staffLoading}
          locale={{ emptyText: "No staff assigned to this project / shift yet. Use Add Staff above." }}
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
                .filter((u) => u.is_active && u.portal === "digitizing")
                .map((u) => ({
                  value: u.id,
                  label: `${u.full_name} — ${u.role.replace(/_/g, " ")} (${u.email})`,
                }))}
            />
          </Form.Item>
          <Form.Item name="shift_id" label="Shift" rules={[{ required: true }]}>
            <Select options={shifts.map((s) => ({ value: s.id, label: `${s.name} (${s.start_time}–${s.end_time})` }))} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
