import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button, Card, Col, Drawer, Input, Row, Select, Tag, message,
  Space, Empty, Typography, Segmented, Badge, Tooltip,
} from "antd";
import { UserAddOutlined } from "@ant-design/icons";
import type { AxiosError } from "axios";
import type { ColumnsType } from "antd/es/table";
import { Table } from "antd";
import api from "@shared/api/client";
import type { Shift, AvailableStaff, UserRecord, StaffBuckets, BucketedStaffMember } from "@shared/types";

const ROLE_COLOR: Record<string, string> = {
  de_staff: "blue",
  de_supervisor: "green",
  customer_supervisor: "purple",
  customer_qc_agent: "orange",
  admin: "red",
};

const ROLE_LABEL: Record<string, string> = {
  de_staff: "DE Staff",
  de_supervisor: "Supervisor",
};

type BucketKey = "unassigned" | "indexer" | "qa";

const BUCKET_TITLE: Record<BucketKey, string> = {
  unassigned: "Unassigned",
  indexer: "Indexer",
  qa: "Quality Assurance",
};

const BUCKET_TARGETS: Record<BucketKey, { key: BucketKey; label: string }[]> = {
  unassigned: [
    { key: "indexer", label: "Indexer" },
    { key: "qa", label: "QA" },
  ],
  indexer: [
    { key: "qa", label: "QA" },
    { key: "unassigned", label: "Unassigned" },
  ],
  qa: [
    { key: "indexer", label: "Indexer" },
    { key: "unassigned", label: "Unassigned" },
  ],
};

interface Props {
  projectId: number;
}

export default function StaffAssignment({ projectId }: Props) {
  const [tableSearch, setTableSearch] = useState("");
  const [selectedShiftId, setSelectedShiftId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerShiftId, setDrawerShiftId] = useState<number | null>(null);
  const [roleFilter, setRoleFilter] = useState("all");
  const [selectedUserIds, setSelectedUserIds] = useState<number[]>([]);
  const [userSearch, setUserSearch] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const qc = useQueryClient();

  const { data: shifts = [] } = useQuery<Shift[]>({
    queryKey: ["project-shifts", projectId],
    queryFn: () => api.get(`/projects/${projectId}/shifts`).then((r) => r.data),
  });

  const { data: allUsers = [] } = useQuery<UserRecord[]>({
    queryKey: ["users"],
    queryFn: () => api.get("/users").then((r) => r.data),
  });

  const { data: buckets, isLoading: bucketsLoading } = useQuery<StaffBuckets>({
    queryKey: ["staff-buckets", projectId, selectedShiftId],
    queryFn: () =>
      api.get(`/projects/${projectId}/staff-buckets`, { params: { shift_id: selectedShiftId } })
        .then((r) => r.data),
    enabled: !!selectedShiftId,
  });

  const { data: drawerAssigned = [] } = useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", projectId, drawerShiftId],
    queryFn: () =>
      api.get(`/projects/${projectId}/available-staff?shift_id=${drawerShiftId}`).then((r) => r.data),
    enabled: !!drawerShiftId,
  });

  const assignStaff = useMutation({
    mutationFn: ({ user_id, shift_id }: { user_id: number; shift_id: number }) =>
      api.post(`/projects/${projectId}/staff`, { user_id, shift_id }),
  });

  const moveBucket = useMutation({
    mutationFn: ({ assignmentId, shiftRole }: { assignmentId: number; shiftRole: BucketKey }) =>
      api.patch(`/staff-assignments/${assignmentId}/bucket`, {
        shift_role: shiftRole === "unassigned" ? null : shiftRole,
      }),
    onSuccess: () => {
      message.success("Staff member moved");
      qc.invalidateQueries({ queryKey: ["staff-buckets", projectId, selectedShiftId] });
    },
    onError: (e: AxiosError<{ detail: string }>) =>
      message.error(e.response?.data?.detail ?? "Could not move staff member"),
  });

  const openDrawer = () => {
    setDrawerShiftId(selectedShiftId);
    setSelectedUserIds([]);
    setUserSearch("");
    setRoleFilter("all");
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setSelectedUserIds([]);
  };

  const handleAssign = async () => {
    if (!drawerShiftId || selectedUserIds.length === 0) return;
    setSubmitting(true);
    const results = await Promise.allSettled(
      selectedUserIds.map((uid) => assignStaff.mutateAsync({ user_id: uid, shift_id: drawerShiftId }))
    );
    setSubmitting(false);
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - succeeded;
    if (succeeded > 0) {
      message.success(`${succeeded} staff member${succeeded > 1 ? "s" : ""} assigned`);
      qc.invalidateQueries({ queryKey: ["available-staff"] });
      qc.invalidateQueries({ queryKey: ["staff-buckets"] });
    }
    if (failed > 0) {
      message.error(`${failed} assignment${failed > 1 ? "s" : ""} failed`);
    }
    closeDrawer();
  };

  const assignedIdSet = useMemo(
    () => new Set(drawerAssigned.map((s) => s.id)),
    [drawerAssigned]
  );

  const digitizingUsers = useMemo(
    () => allUsers.filter((u) => u.is_active && u.portal === "digitizing"),
    [allUsers]
  );

  const roleOptions = useMemo(() => {
    const roles = [...new Set(digitizingUsers.map((u) => u.role))];
    return [
      { label: "All", value: "all" },
      ...roles.map((r) => ({ label: ROLE_LABEL[r] ?? r.replace(/_/g, " "), value: r })),
    ];
  }, [digitizingUsers]);

  const filteredUsers = useMemo(() => {
    const q = userSearch.toLowerCase();
    return digitizingUsers.filter((u) => {
      const matchesRole = roleFilter === "all" || u.role === roleFilter;
      const matchesSearch =
        !q ||
        u.full_name.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q);
      return matchesRole && matchesSearch;
    });
  }, [digitizingUsers, userSearch, roleFilter]);

  // Sort: unassigned first, already-assigned at bottom
  const sortedUsers = useMemo(
    () => [...filteredUsers].sort((a, b) => {
      const aAssigned = assignedIdSet.has(a.id) ? 1 : 0;
      const bAssigned = assignedIdSet.has(b.id) ? 1 : 0;
      return aAssigned - bAssigned;
    }),
    [filteredUsers, assignedIdSet]
  );

  const userColumns: ColumnsType<UserRecord> = [
    {
      title: "Name",
      dataIndex: "full_name",
      render: (name: string, u: UserRecord) => (
        <Space>
          <span style={assignedIdSet.has(u.id) ? { color: "#bfbfbf" } : undefined}>{name}</span>
          {assignedIdSet.has(u.id) && <Tag color="default">Assigned</Tag>}
        </Space>
      ),
    },
    {
      title: "Email",
      dataIndex: "email",
      render: (email: string, u: UserRecord) => (
        <span style={assignedIdSet.has(u.id) ? { color: "#bfbfbf" } : undefined}>{email}</span>
      ),
    },
    {
      title: "Role",
      dataIndex: "role",
      render: (v: string, u: UserRecord) => (
        <Tag color={assignedIdSet.has(u.id) ? "default" : (ROLE_COLOR[v] ?? "default")}>
          {(ROLE_LABEL[v] ?? v).replace(/_/g, " ")}
        </Tag>
      ),
    },
  ];

  const q = tableSearch.toLowerCase();
  const matchesSearch = (m: BucketedStaffMember) =>
    !q || m.full_name.toLowerCase().includes(q) || m.email.toLowerCase().includes(q);

  const bucketColumns = (bucketKey: BucketKey): ColumnsType<BucketedStaffMember> => [
    {
      title: "Name",
      dataIndex: "full_name",
      ellipsis: true,
      render: (name: string, m: BucketedStaffMember) => (
        <Space direction="vertical" size={0} style={{ maxWidth: "100%" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
          {m.has_active_work && <Tag color="orange" style={{ marginTop: 2 }}>Active task</Tag>}
        </Space>
      ),
    },
    {
      title: "Move to",
      key: "move",
      width: 110,
      render: (_: unknown, m: BucketedStaffMember) => (
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          {BUCKET_TARGETS[bucketKey].map((target) => {
            const btn = (
              <Button
                key={target.key}
                size="small"
                block
                disabled={m.has_active_work}
                loading={moveBucket.isPending && moveBucket.variables?.assignmentId === m.assignment_id}
                onClick={() => moveBucket.mutate({ assignmentId: m.assignment_id, shiftRole: target.key })}
              >
                → {target.label}
              </Button>
            );
            return m.has_active_work ? (
              <Tooltip
                key={target.key}
                title="Complete or reassign this user's active task before moving them"
              >
                <span style={{ display: "block" }}>{btn}</span>
              </Tooltip>
            ) : btn;
          })}
        </Space>
      ),
    },
  ];

  const selectedShift = shifts.find((s) => s.id === drawerShiftId);

  const bucketKeys: BucketKey[] = ["unassigned", "indexer", "qa"];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <span style={{ fontSize: 20, fontWeight: 600 }}>Staff Assignment</span>
        <Button type="primary" icon={<UserAddOutlined />} onClick={openDrawer}>
          Add Staff
        </Button>
      </Space>

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="Select shift to manage staff roles"
          style={{ width: 280 }}
          options={shifts.map((s) => ({
            value: s.id,
            label: `${s.name} (${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)})`,
          }))}
          onChange={setSelectedShiftId}
          value={selectedShiftId}
          allowClear
        />
      </Space>

      {selectedShiftId ? (
        <>
          <Input.Search
            placeholder="Search by name or email…"
            allowClear
            onChange={(e) => setTableSearch(e.target.value)}
            style={{ marginBottom: 12, maxWidth: 360 }}
          />
          <Row gutter={16}>
            {bucketKeys.map((key) => (
              <Col key={key} xs={24} lg={8} style={{ marginBottom: 16 }}>
                <Card title={`${BUCKET_TITLE[key]} (${buckets?.[key]?.length ?? 0})`} size="small">
                  <Table
                    dataSource={(buckets?.[key] ?? []).filter(matchesSearch)}
                    columns={bucketColumns(key)}
                    rowKey="assignment_id"
                    loading={bucketsLoading}
                    size="small"
                    pagination={false}
                    tableLayout="fixed"
                    locale={{ emptyText: "No staff in this bucket" }}
                  />
                </Card>
              </Col>
            ))}
          </Row>
        </>
      ) : (
        <Empty description="Select a shift to manage staff roles" />
      )}

      <Drawer
        title={
          <Space direction="vertical" size={0}>
            <span>Add Staff to Project</span>
            {selectedShift && (
              <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
                Shift: {selectedShift.name} ({selectedShift.start_time.slice(0, 5)}–{selectedShift.end_time.slice(0, 5)})
              </Typography.Text>
            )}
          </Space>
        }
        open={drawerOpen}
        onClose={closeDrawer}
        width={660}
        extra={
          <Button
            type="primary"
            disabled={selectedUserIds.length === 0 || !drawerShiftId}
            loading={submitting}
            onClick={handleAssign}
          >
            {selectedUserIds.length > 0
              ? `Assign ${selectedUserIds.length} user${selectedUserIds.length > 1 ? "s" : ""}`
              : "Select users below"}
          </Button>
        }
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Select
            placeholder="Select shift"
            style={{ width: "100%" }}
            options={shifts.map((s) => ({
              value: s.id,
              label: `${s.name} (${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)})`,
            }))}
            value={drawerShiftId}
            onChange={(id) => {
              setDrawerShiftId(id);
              setSelectedUserIds([]);
            }}
          />

          <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
            <Input.Search
              placeholder="Search by name or email…"
              allowClear
              value={userSearch}
              onChange={(e) => setUserSearch(e.target.value)}
              style={{ width: 260 }}
            />
            <Segmented
              options={roleOptions}
              value={roleFilter}
              onChange={(v) => setRoleFilter(v as string)}
            />
          </Space>

          {selectedUserIds.length > 0 && (
            <Typography.Text type="secondary">
              <Badge count={selectedUserIds.length} color="blue" /> user{selectedUserIds.length > 1 ? "s" : ""} selected
            </Typography.Text>
          )}

          <Table
            dataSource={sortedUsers}
            columns={userColumns}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 15, showSizeChanger: false }}
            rowSelection={{
              selectedRowKeys: selectedUserIds,
              onChange: (keys) => setSelectedUserIds(keys as number[]),
              getCheckboxProps: (u: UserRecord) => ({
                disabled: assignedIdSet.has(u.id),
              }),
            }}
            rowClassName={(u: UserRecord) =>
              assignedIdSet.has(u.id) ? "ant-table-row-disabled" : ""
            }
            locale={{ emptyText: "No users match your filters." }}
          />
        </Space>
      </Drawer>
    </>
  );
}
