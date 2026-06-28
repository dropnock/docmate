import { useState, useEffect } from "react";
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
import api from "@shared/api/client";
import type { UserRecord, Project } from "@shared/types";

const { Header, Sider, Content } = Layout;
const qc = new QueryClient();

const SUPERVISOR_PAGES = [
  { key: "batches", label: "Batch Manager", icon: <InboxOutlined /> },
  { key: "staff-assignment", label: "Staff Assignment", icon: <UsergroupAddOutlined /> },
  { key: "assign", label: "Assign Tasks", icon: <UnorderedListOutlined /> },
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
  { key: "organisations", label: "Organisations", icon: <BankOutlined /> },
  { key: "shifts", label: "Shifts", icon: <ClockCircleOutlined /> },
  { key: "users", label: "Users", icon: <UserOutlined /> },
];

const PROJECT_SCOPED_PAGES = new Set(["batches", "staff-assignment", "assign", "stale", "productivity", "kpi"]);

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

  return (
    <Layout style={{ minHeight: "100vh" }}>
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
              {page === "batches" && projectId && (
                <BatchManager projectId={projectId} isAdmin={isAdmin} />
              )}
              {page === "staff-assignment" && projectId && isSupervisor && (
                <StaffAssignment />
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
              {page === "history" && isSupervisor && <RecordHistory recordId={1} />}
              {page === "mytasks" && isAgent && <MyTasks />}
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
