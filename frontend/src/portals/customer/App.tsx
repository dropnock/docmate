import { lazy, Suspense, useState, useEffect } from "react";
import { Layout, Menu, Spin, Result } from "antd";
import { ProjectOutlined, CheckCircleOutlined, HistoryOutlined, InboxOutlined } from "@ant-design/icons";
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
import PageSkeleton from "@shared/components/PageSkeleton";
import AppHeader from "@shared/components/AppHeader";
import api from "@shared/api/client";
import type { UserRecord } from "@shared/types";

const ProjectKPIDashboard = lazy(() => import("../digitizing/pages/ProjectKPIDashboard"));
const RecordHistory = lazy(() => import("../digitizing/pages/RecordHistory"));
const QCWorkspace = lazy(() => import("./pages/QCWorkspace"));
const CustomerLotManager = lazy(() => import("./pages/CustomerLotManager"));

const { Sider, Content } = Layout;
const queryClient = createQueryClient();

const NAV_ITEMS = [
  { key: "/lots", label: "Lots", icon: <InboxOutlined /> },
  { key: "/qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "/kpis", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "/history", label: "Record History", icon: <HistoryOutlined /> },
];

const FULL_BLEED_ROUTES = new Set(["/qc"]);

function getSubdomain(): string | null {
  const parts = window.location.hostname.split(".");
  return parts.length >= 2 ? parts[0] : null;
}

function AppInner() {
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const { data: user } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  const isWorkspacePage = FULL_BLEED_ROUTES.has(location.pathname);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <AppHeader title="DocMate — Customer Portal" userName={user?.full_name} onSignOut={logout} />
      <Layout>
        <Sider
          width={220}
          collapsedWidth={64}
          collapsible
          collapsed={siderCollapsed}
          onCollapse={(v) => setSiderCollapsed(v)}
          breakpoint="lg"
          theme="light"
        >
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            items={NAV_ITEMS}
            onClick={({ key }) => navigate(`${key}${location.search}`)}
          />
        </Sider>
        <Content style={{ padding: isWorkspacePage ? 0 : 24 }}>
          <Suspense fallback={<PageSkeleton />}>
            <Routes>
              <Route path="/" element={<Navigate to="/qc" replace />} />
              <Route
                path="/lots"
                element={
                  <ProjectScopedRoute>
                    {(projectId) => (
                      <CustomerLotManager projectId={projectId} role={user?.role ?? ""} />
                    )}
                  </ProjectScopedRoute>
                }
              />
              <Route path="/qc" element={<QCWorkspace />} />
              <Route
                path="/kpis"
                element={
                  <ProjectScopedRoute>
                    {(projectId) => <ProjectKPIDashboard projectId={projectId} />}
                  </ProjectScopedRoute>
                }
              />
              <Route
                path="/history"
                element={
                  <ProjectScopedRoute>
                    {(projectId) => <RecordHistory projectId={projectId} />}
                  </ProjectScopedRoute>
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

type AppState = "loading" | "ready" | "error";

export default function App() {
  const [state, setState] = useState<AppState>("loading");
  const [errorMsg, setErrorMsg] = useState<string>("");

  useEffect(() => {
    const subdomain = getSubdomain();
    if (!subdomain) {
      setErrorMsg("No customer subdomain detected in the URL.");
      setState("error");
      return;
    }

    fetch(`/api/auth/realm-by-subdomain?subdomain=${encodeURIComponent(subdomain)}`)
      .then((r) => {
        if (!r.ok) throw new Error("not_found");
        return r.json();
      })
      .then(({ realm_slug }: { realm_slug: string }) =>
        initKeycloak(realm_slug, "docmate-cust")
      )
      .then(() => setState("ready"))
      .catch((err) => {
        const msg =
          err.message === "not_found"
            ? `"${subdomain}" is not a recognised customer portal.`
            : "Failed to connect to the authentication service.";
        setErrorMsg(msg);
        setState("error");
      });
  }, []);

  if (state === "loading") return <Spin fullscreen tip="Connecting..." />;

  if (state === "error") {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
        <Result status="404" title="Unknown Portal" subTitle={errorMsg} />
      </div>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppInner />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
