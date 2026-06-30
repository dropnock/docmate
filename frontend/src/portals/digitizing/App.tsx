import { useState, useEffect } from "react";
import { WorkspaceErrorBoundary } from "@shared/components/WorkspaceErrorBoundary";
import { Layout, Menu, Typography, Button, Select, Space, Spin } from "antd";
import {
  ProjectOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  WarningOutlined,
  HistoryOutlined,
  FolderOpenOutlined,
  UserOutlined,
  UsergroupAddOutlined,
  BankOutlined,
  InboxOutlined,
  CheckSquareOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initKeycloak, logout } from "@shared/api/keycloak";
import StaffProductivityDashboard from "./pages/StaffProductivityDashboard";
import ProjectKPIDashboard from "./pages/ProjectKPIDashboard";
import StaleTaskManager from "./pages/StaleTaskManager";
import RecordHistory from "./pages/RecordHistory";
import TaskAssignment from "./pages/TaskAssignment";
import ProjectsManager from "./pages/ProjectsManager";
import UsersManager from "./pages/UsersManager";
import StaffAssignment from "./pages/StaffAssignment";
import OrganisationsManager from "./pages/OrganisationsManager";
import BatchManager from "./pages/BatchManager";
import ShiftsManager from "./pages/ShiftsManager";
import MyTasks from "./pages/MyTasks";
import CabinetManager from "./pages/CabinetManager";
import CabinetAssignment from "./pages/CabinetAssignment";
import LotManager from "./pages/LotManager";
import api from "@shared/api/client";
import type { UserRecord, Project } from "@shared/types";

const { Header, Sider, Content } = Layout;
const qc = new QueryClient();

const SUPERVISOR_PAGES = [
  { key: "cabinet-assign", label: "Cabinet Assignment", icon: <InboxOutlined /> },
  { key: "lots", label: "Lots", icon: <UnorderedListOutlined /> },
  { key: "batches", label: "Batch Manager (Legacy)", icon: <InboxOutlined /> },
  { key: "staff-assignment", label: "Staff Assignment", icon: <UsergroupAddOutlined /> },
  { key: "assign", label: "Assign Tasks (Legacy)", icon: <UnorderedListOutlined /> },
  { key: "stale", label: "Stale Tasks", icon: <WarningOutlined /> },
  { key: "productivity", label: "Staff Productivity", icon: <TeamOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const AGENT_PAGES = [
  { key: "mytasks", label: "My Tasks", icon: <CheckSquareOutlined /> },
];

const ADMIN_PAGES = [
  { key: "projects", label: "Projects", icon: <FolderOpenOutlined /> },
  { key: "cabinets", label: "Cabinets", icon: <FolderOpenOutlined /> },
  { key: "organisations", label: "Organisations", icon: <BankOutlined /> },
  { key: "shifts", label: "Shifts", icon: <ClockCircleOutlined /> },
  { key: "users", label: "Users", icon: <UserOutlined /> },
];

const PROJECT_SCOPED_PAGES = new Set([
  "batches", "staff-assignment", "assign", "stale", "productivity", "kpi", "history",
  "cabinet-assign", "lots", "cabinets",
]);

function ProjectSelector({
  projectId,
  onChange,
}: {
  projectId: number | null;
  onChange: (id: number) => void;
}) {
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });
  return (
    <Select
      placeholder="Select project"
      style={{ width: 220 }}
      value={projectId}
      onChange={onChange}
      options={projects.map((p) => ({ value: p.id, label: p.name }))}
    />
  );
}

function AppInner() {
  const [page, setPage] = useState("productivity");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [siderCollapsed, setSiderCollapsed] = useState(false);

  const { data: user, isLoading } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  if (isLoading || !user) return <Spin fullscreen tip="Loading profile..." />;

  const isAdmin = user.role === "admin";
  const isSupervisor = isAdmin || user.role === "de_supervisor";
  const isAgent = ["de_indexer", "de_qa_agent"].includes(user.role);

  const navItems = [
    ...(isSupervisor
      ? [{ type: "group" as const, label: "Operations", children: SUPERVISOR_PAGES }]
      : []),
    ...(isAgent
      ? [{ type: "group" as const, label: "Workspace", children: AGENT_PAGES }]
      : []),
    ...(isAdmin
      ? [{ type: "group" as const, label: "Admin", children: ADMIN_PAGES }]
      : []),
  ];

  const showProjectSelector = PROJECT_SCOPED_PAGES.has(page);

  // When an agent is in the workspace, give the full area to the split-pane
  const isWorkspacePage = isAgent && page === "mytasks";

  return (
    <Layout style={{ height: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          DocMate — Digitizing
        </Typography.Title>
        <Space>
          <Typography.Text style={{ color: "rgba(255,255,255,0.65)" }}>
            {user.full_name}
          </Typography.Text>
          <Button onClick={logout} type="text" style={{ color: "white" }}>
            Sign Out
          </Button>
        </Space>
      </Header>

      <Layout style={{ height: "calc(100vh - 64px)" }}>
        <Sider
          width={220}
          collapsedWidth={64}
          collapsible
          collapsed={siderCollapsed}
          onCollapse={(v) => setSiderCollapsed(v)}
          theme="light"
          style={{ overflow: "auto" }}
        >
          <Menu
            mode="inline"
            selectedKeys={[page]}
            items={navItems}
            onClick={({ key }) => setPage(key)}
            style={{ height: "100%", borderRight: 0 }}
          />
        </Sider>

        <Content
          style={{
            height: "100%",
            padding: isWorkspacePage ? 0 : 24,
            overflow: isWorkspacePage ? "hidden" : "auto",
            boxSizing: "border-box",
          }}
        >
          {showProjectSelector && (
            <div style={{ marginBottom: 20 }}>
              <Space>
                <span style={{ color: "#595959" }}>Project:</span>
                <ProjectSelector projectId={projectId} onChange={setProjectId} />
              </Space>
            </div>
          )}

          {showProjectSelector && !projectId ? (
            <div style={{ color: "#8c8c8c", marginTop: 40, textAlign: "center" }}>
              Select a project above to continue.
            </div>
          ) : (
            <>
              {page === "cabinets" && projectId && isAdmin && (
                <CabinetManager projectId={projectId} />
              )}
              {page === "cabinet-assign" && projectId && isSupervisor && (
                <CabinetAssignment projectId={projectId} />
              )}
              {page === "lots" && projectId && isSupervisor && (
                <LotManager projectId={projectId} />
              )}
              {page === "batches" && projectId && (
                <BatchManager projectId={projectId} isAdmin={isAdmin} />
              )}
              {page === "staff-assignment" && projectId && isSupervisor && (
                <StaffAssignment projectId={projectId} />
              )}
              {page === "assign" && projectId && isSupervisor && (
                <TaskAssignment projectId={projectId} />
              )}
              {page === "stale" && projectId && isSupervisor && (
                <StaleTaskManager projectId={projectId} />
              )}
              {page === "productivity" && projectId && isSupervisor && (
                <StaffProductivityDashboard projectId={projectId} />
              )}
              {page === "kpi" && projectId && isSupervisor && (
                <ProjectKPIDashboard projectId={projectId} />
              )}
              {page === "history" && projectId && isSupervisor && <RecordHistory projectId={projectId} />}
              {page === "mytasks" && isAgent && (
                <WorkspaceErrorBoundary>
                  <MyTasks />
                </WorkspaceErrorBoundary>
              )}
              {page === "projects" && isAdmin && (
                <ProjectsManager
                  onOpen={(id) => { setProjectId(id); setPage("batches"); }}
                />
              )}
              {page === "organisations" && isAdmin && <OrganisationsManager />}
              {page === "shifts" && isAdmin && <ShiftsManager />}
              {page === "users" && isAdmin && <UsersManager />}
            </>
          )}
        </Content>
      </Layout>
    </Layout>
  );
}

export default function App() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    initKeycloak("doc", "docmate-de")
      .then(() => setReady(true))
      .catch((err) => console.error("Keycloak init failed:", err));
  }, []);

  if (!ready) return <Spin fullscreen tip="Connecting..." />;

  return (
    <QueryClientProvider client={qc}>
      <AppInner />
    </QueryClientProvider>
  );
}
