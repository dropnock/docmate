import { useState, useEffect } from "react";
import { Layout, Menu, Typography, Button, Select, Space, Spin, Result } from "antd";
import { ProjectOutlined, CheckCircleOutlined, HistoryOutlined, InboxOutlined } from "@ant-design/icons";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initKeycloak, logout } from "@shared/api/keycloak";
import ProjectKPIDashboard from "../digitizing/pages/ProjectKPIDashboard";
import RecordHistory from "../digitizing/pages/RecordHistory";
import QCWorkspace from "./pages/QCWorkspace";
import CustomerLotManager from "./pages/CustomerLotManager";
import api from "@shared/api/client";
import type { Project, UserRecord } from "@shared/types";

const { Header, Sider, Content } = Layout;
const queryClient = new QueryClient();

const NAV_ITEMS = [
  { key: "lots", label: "Lots", icon: <InboxOutlined /> },
  { key: "qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const PROJECT_SCOPED = new Set(["kpi", "history", "lots"]);

function getSubdomain(): string | null {
  const parts = window.location.hostname.split(".");
  return parts.length >= 2 ? parts[0] : null;
}

function AppInner() {
  const [page, setPage] = useState("qc");
  const [projectId, setProjectId] = useState<number | null>(null);

  const { data: user } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
  });

  const showProjectSelector = PROJECT_SCOPED.has(page);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          DocMate — Customer Portal
        </Typography.Title>
        <Space>
          {user && (
            <Typography.Text style={{ color: "rgba(255,255,255,0.65)" }}>
              {user.full_name}
            </Typography.Text>
          )}
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
            items={NAV_ITEMS}
            onClick={({ key }) => setPage(key)}
          />
        </Sider>
        <Content style={{ padding: page === "qc" ? 0 : 24 }}>
          {showProjectSelector && (
            <div style={{ marginBottom: 20 }}>
              <Space>
                <span style={{ color: "#595959" }}>Project:</span>
                <Select
                  placeholder="Select project"
                  style={{ width: 220 }}
                  value={projectId}
                  onChange={setProjectId}
                  options={projects.map((p) => ({ value: p.id, label: p.name }))}
                />
              </Space>
            </div>
          )}
          {page === "lots" && projectId && <CustomerLotManager projectId={projectId} />}
          {page === "qc" && <QCWorkspace />}
          {page === "kpi" && projectId && <ProjectKPIDashboard projectId={projectId} />}
          {showProjectSelector && !projectId && (
            <div style={{ color: "#8c8c8c", textAlign: "center", marginTop: 40 }}>
              Select a project above to continue.
            </div>
          )}
          {page === "history" && projectId && <RecordHistory projectId={projectId} />}
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
      <AppInner />
    </QueryClientProvider>
  );
}
