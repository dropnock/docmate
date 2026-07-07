import { lazy, Suspense, useState, useEffect } from "react";
import { WorkspaceErrorBoundary } from "@shared/components/WorkspaceErrorBoundary";
import { Layout, Menu, Spin } from "antd";
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
import { useQuery, QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "@shared/query/queryClient";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useNavigate,
  useLocation,
} from "react-router-dom";
import { initKeycloak, logout } from "@shared/api/keycloak";
import ProjectScopedRoute from "@shared/routing/ProjectScopedRoute";
import RequireRole from "@shared/routing/RequireRole";
import PageSkeleton from "@shared/components/PageSkeleton";
import AppHeader from "@shared/components/AppHeader";
import api from "@shared/api/client";
import type { UserRecord } from "@shared/types";

const StaffProductivityDashboard = lazy(() => import("./pages/StaffProductivityDashboard"));
const ProjectKPIDashboard = lazy(() => import("./pages/ProjectKPIDashboard"));
const StaleTaskManager = lazy(() => import("./pages/StaleTaskManager"));
const RecordHistory = lazy(() => import("./pages/RecordHistory"));
const ProjectsManager = lazy(() => import("./pages/ProjectsManager"));
const UsersManager = lazy(() => import("./pages/UsersManager"));
const StaffAssignment = lazy(() => import("./pages/StaffAssignment"));
const OrganisationsManager = lazy(() => import("./pages/OrganisationsManager"));
const ShiftsManager = lazy(() => import("./pages/ShiftsManager"));
const MyTasks = lazy(() => import("./pages/MyTasks"));
const CabinetManager = lazy(() => import("./pages/CabinetManager"));
const CabinetAssignment = lazy(() => import("./pages/CabinetAssignment"));
const LotManager = lazy(() => import("./pages/LotManager"));

const { Sider, Content } = Layout;
const qc = createQueryClient();

const SUPERVISOR_PAGES = [
  { key: "/cabinet-assignment", label: "Cabinet Assignment", icon: <InboxOutlined /> },
  { key: "/lots", label: "Lots", icon: <UnorderedListOutlined /> },
  { key: "/staff-assignment", label: "Staff Assignment", icon: <UsergroupAddOutlined /> },
  { key: "/stale-tasks", label: "Stale Tasks", icon: <WarningOutlined /> },
  { key: "/productivity", label: "Staff Productivity", icon: <TeamOutlined /> },
  { key: "/kpis", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "/history", label: "Record History", icon: <HistoryOutlined /> },
];

const AGENT_PAGES = [
  { key: "/my-tasks", label: "My Tasks", icon: <CheckSquareOutlined /> },
];

const ADMIN_PAGES = [
  { key: "/projects", label: "Projects", icon: <FolderOpenOutlined /> },
  { key: "/cabinets", label: "Cabinets", icon: <FolderOpenOutlined /> },
  { key: "/organisations", label: "Organisations", icon: <BankOutlined /> },
  { key: "/shifts", label: "Shifts", icon: <ClockCircleOutlined /> },
  { key: "/users", label: "Users", icon: <UserOutlined /> },
];

const FULL_BLEED_ROUTES = new Set(["/my-tasks"]);

function AppInner() {
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const { data: user, isLoading } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  if (isLoading || !user) return <Spin fullscreen tip="Loading profile..." />;

  const isAdmin = user.role === "admin";
  const isSupervisor = isAdmin || user.role === "de_supervisor";
  const isAgent = user.role === "de_staff";

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

  const isWorkspacePage = FULL_BLEED_ROUTES.has(location.pathname);
  const defaultRoute = isSupervisor ? "/productivity" : "/my-tasks";

  return (
    <Layout style={{ height: "100vh" }}>
      <AppHeader title="DocMate — Digitizing" userName={user.full_name} onSignOut={logout} />

      <Layout style={{ height: "calc(100vh - 64px)" }}>
        <Sider
          width={220}
          collapsedWidth={64}
          collapsible
          collapsed={siderCollapsed}
          onCollapse={(v) => setSiderCollapsed(v)}
          breakpoint="lg"
          theme="light"
          style={{ overflow: "auto" }}
        >
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            items={navItems}
            onClick={({ key }) => navigate(`${key}${location.search}`)}
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
          <Suspense fallback={<PageSkeleton />}>
            <Routes>
              <Route path="/" element={<Navigate to={defaultRoute} replace />} />

              <Route
                path="/cabinets"
                element={
                  <RequireRole allow={isAdmin}>
                    <ProjectScopedRoute>
                      {(projectId) => <CabinetManager projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/cabinet-assignment"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <CabinetAssignment projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/lots"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <LotManager projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/staff-assignment"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <StaffAssignment projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/stale-tasks"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <StaleTaskManager projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/productivity"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <StaffProductivityDashboard projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/kpis"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <ProjectKPIDashboard projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/history"
                element={
                  <RequireRole allow={isSupervisor}>
                    <ProjectScopedRoute>
                      {(projectId) => <RecordHistory projectId={projectId} />}
                    </ProjectScopedRoute>
                  </RequireRole>
                }
              />
              <Route
                path="/my-tasks"
                element={
                  <RequireRole allow={isAgent}>
                    <WorkspaceErrorBoundary>
                      <MyTasks />
                    </WorkspaceErrorBoundary>
                  </RequireRole>
                }
              />
              <Route
                path="/projects"
                element={
                  <RequireRole allow={isAdmin}>
                    <ProjectsManager
                      onOpen={(id) => navigate(`/cabinets?project=${id}`)}
                    />
                  </RequireRole>
                }
              />
              <Route
                path="/organisations"
                element={
                  <RequireRole allow={isAdmin}>
                    <OrganisationsManager />
                  </RequireRole>
                }
              />
              <Route
                path="/shifts"
                element={
                  <RequireRole allow={isAdmin}>
                    <ShiftsManager />
                  </RequireRole>
                }
              />
              <Route
                path="/users"
                element={
                  <RequireRole allow={isAdmin}>
                    <UsersManager />
                  </RequireRole>
                }
              />

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
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
      <BrowserRouter>
        <AppInner />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
