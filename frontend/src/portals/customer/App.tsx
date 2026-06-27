import { useState, useEffect } from "react";
import { Layout, Menu, Typography, Button, Select, Space, Spin, Card, List } from "antd";
import { ProjectOutlined, CheckCircleOutlined, HistoryOutlined } from "@ant-design/icons";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initKeycloak, logout } from "@shared/api/keycloak";
import ProjectKPIDashboard from "../digitizing/pages/ProjectKPIDashboard";
import RecordHistory from "../digitizing/pages/RecordHistory";
import QCWorkspace from "./pages/QCWorkspace";
import api from "@shared/api/client";
import type { Project, UserRecord } from "@shared/types";

const { Header, Sider, Content } = Layout;
const queryClient = new QueryClient();

const CUSTOMER_REALM_KEY = "docmate_customer_realm";

const NAV_ITEMS = [
  { key: "qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const PROJECT_SCOPED = new Set(["kpi"]);

interface CustomerRealm {
  name: string;
  realm_slug: string;
}

function OrgSelector({ onSelect }: { onSelect: (slug: string) => void }) {
  const [realms, setRealms] = useState<CustomerRealm[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/auth/customer-realms")
      .then((r) => r.json())
      .then((data: CustomerRealm[]) => {
        setRealms(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "#f0f2f5",
      }}
    >
      <Card title="Select Your Organisation" style={{ width: 380 }} loading={loading}>
        <List
          dataSource={realms}
          locale={{ emptyText: "No organisations available" }}
          renderItem={(r) => (
            <List.Item>
              <Button
                type="link"
                block
                style={{ textAlign: "left" }}
                onClick={() => onSelect(r.realm_slug)}
              >
                {r.name}
              </Button>
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
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
  const [realm, setRealm] = useState<string | null>(
    () => localStorage.getItem(CUSTOMER_REALM_KEY)
  );
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!realm) return;
    initKeycloak(realm, "docmate-cust")
      .then(() => setReady(true))
      .catch((err) => {
        console.error("Keycloak init failed:", err);
        localStorage.removeItem(CUSTOMER_REALM_KEY);
        setRealm(null);
      });
  }, [realm]);

  if (!realm) {
    return (
      <OrgSelector
        onSelect={(slug) => {
          localStorage.setItem(CUSTOMER_REALM_KEY, slug);
          setRealm(slug);
        }}
      />
    );
  }

  if (!ready) return <Spin fullscreen tip="Connecting..." />;

  return (
    <QueryClientProvider client={queryClient}>
      <AppInner />
    </QueryClientProvider>
  );
}
