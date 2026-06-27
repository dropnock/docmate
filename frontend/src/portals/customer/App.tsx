import { useState } from "react";
import { Layout, Menu, Typography, Button, Select, Space } from "antd";
import { ProjectOutlined, CheckCircleOutlined, HistoryOutlined } from "@ant-design/icons";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LoginPage from "@shared/components/LoginPage";
import ProjectKPIDashboard from "../digitizing/pages/ProjectKPIDashboard";
import RecordHistory from "../digitizing/pages/RecordHistory";
import QCWorkspace from "./pages/QCWorkspace";
import { getStoredUser, logout } from "@shared/api/auth";
import api from "@shared/api/client";
import type { AuthUser, Project } from "@shared/types";

const { Header, Sider, Content } = Layout;
const queryClient = new QueryClient();

const NAV_ITEMS = [
  { key: "qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const PROJECT_SCOPED = new Set(["kpi"]);

function AppInner() {
  const [user, setUser] = useState<AuthUser | null>(getStoredUser);
  const [page, setPage] = useState("qc");
  const [projectId, setProjectId] = useState<number | null>(null);

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
    enabled: !!user,
  });

  if (!user) return <LoginPage onLogin={setUser} portalLabel="Customer" />;

  const showProjectSelector = PROJECT_SCOPED.has(page);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          DocMate — Customer Portal
        </Typography.Title>
        <Button onClick={logout} type="text" style={{ color: "white" }}>
          Sign Out
        </Button>
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
          {page === "qc" && <QCWorkspace />}
          {page === "kpi" && projectId && <ProjectKPIDashboard projectId={projectId} />}
          {page === "kpi" && !projectId && (
            <div style={{ color: "#8c8c8c", textAlign: "center", marginTop: 40 }}>
              Select a project above to view KPIs.
            </div>
          )}
          {page === "history" && <RecordHistory recordId={1} />}
        </Content>
      </Layout>
    </Layout>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppInner />
    </QueryClientProvider>
  );
}
