import { useState } from "react";
import { Layout, Menu, Typography, Button, Select, Space } from "antd";
import {
  ProjectOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  WarningOutlined,
  HistoryOutlined,
  FolderOpenOutlined,
  UserOutlined,
  UsergroupAddOutlined,
} from "@ant-design/icons";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LoginPage from "@shared/components/LoginPage";
import StaffProductivityDashboard from "./pages/StaffProductivityDashboard";
import ProjectKPIDashboard from "./pages/ProjectKPIDashboard";
import StaleTaskManager from "./pages/StaleTaskManager";
import RecordHistory from "./pages/RecordHistory";
import TaskAssignment from "./pages/TaskAssignment";
import ProjectsManager from "./pages/ProjectsManager";
import UsersManager from "./pages/UsersManager";
import StaffAssignment from "./pages/StaffAssignment";
import { getStoredUser, logout } from "@shared/api/auth";
import api from "@shared/api/client";
import type { AuthUser, Project } from "@shared/types";

const { Header, Sider, Content } = Layout;
const qc = new QueryClient();

const SUPERVISOR_PAGES = [
  { key: "productivity", label: "Staff Productivity", icon: <TeamOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "assign", label: "Assign Tasks", icon: <UnorderedListOutlined /> },
  { key: "stale", label: "Stale Tasks", icon: <WarningOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const ADMIN_PAGES = [
  { key: "projects", label: "Projects", icon: <FolderOpenOutlined /> },
  { key: "users", label: "Users", icon: <UserOutlined /> },
  { key: "staff-assignment", label: "Staff Assignment", icon: <UsergroupAddOutlined /> },
];

// Pages that require a project to be selected
const PROJECT_SCOPED_PAGES = new Set(["productivity", "kpi", "assign", "stale"]);

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
  const [user, setUser] = useState<AuthUser | null>(getStoredUser);
  const [page, setPage] = useState("productivity");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [inspectRecordId] = useState<number>(1);

  if (!user) return <LoginPage onLogin={setUser} portalLabel="Digitizing Entity" />;

  const isAdmin = user.role === "admin";
  const navItems = isAdmin
    ? [
        { type: "group" as const, label: "Operations", children: SUPERVISOR_PAGES },
        { type: "group" as const, label: "Admin", children: ADMIN_PAGES },
      ]
    : SUPERVISOR_PAGES;

  const showProjectSelector = PROJECT_SCOPED_PAGES.has(page);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
      >
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          DocMate — Digitizing
        </Typography.Title>
        <Space>
          <Typography.Text style={{ color: "rgba(255,255,255,0.65)" }}>
            {user.full_name || `User ${user.user_id}`}
          </Typography.Text>
          <Button onClick={logout} type="text" style={{ color: "white" }}>
            Sign Out
          </Button>
        </Space>
      </Header>

      <Layout>
        <Sider width={220} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[page]}
            items={navItems}
            onClick={({ key }) => setPage(key)}
            style={{ height: "100%", borderRight: 0 }}
          />
        </Sider>

        <Content style={{ padding: 24 }}>
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
              {page === "productivity" && projectId && (
                <StaffProductivityDashboard projectId={projectId} />
              )}
              {page === "kpi" && projectId && (
                <ProjectKPIDashboard projectId={projectId} />
              )}
              {page === "assign" && projectId && (
                <TaskAssignment projectId={projectId} />
              )}
              {page === "stale" && projectId && (
                <StaleTaskManager projectId={projectId} />
              )}
              {page === "history" && (
                <RecordHistory recordId={inspectRecordId} />
              )}
              {page === "projects" && isAdmin && <ProjectsManager />}
              {page === "users" && isAdmin && <UsersManager />}
              {page === "staff-assignment" && isAdmin && <StaffAssignment />}
            </>
          )}
        </Content>
      </Layout>
    </Layout>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AppInner />
    </QueryClientProvider>
  );
}
