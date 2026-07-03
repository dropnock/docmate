import { useState, useEffect } from "react";
import { Layout, Menu, Typography, Button, Select, Space, Spin, Card, Form, Input, Alert } from "antd";
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

const CUSTOMER_REALM_KEY = "docmate_customer_realm";

const NAV_ITEMS = [
  { key: "lots", label: "Lots", icon: <InboxOutlined /> },
  { key: "qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const PROJECT_SCOPED = new Set(["kpi", "history", "lots"]);

function EmailGate({ onRealm }: { onRealm: (slug: string) => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async ({ email }: { email: string }) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/auth/realm-by-domain?email=${encodeURIComponent(email)}`);
      if (!res.ok) {
        setError("No organisation found for this email address.");
        return;
      }
      const { realm_slug } = await res.json() as { realm_slug: string };
      onRealm(realm_slug);
    } catch {
      setError("Unable to reach the server. Please try again.");
    } finally {
      setLoading(false);
    }
  };

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
      <Card title="DocMate — Customer Portal" style={{ width: 380 }}>
        <Form layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            name="email"
            label="Work email address"
            rules={[
              { required: true, message: "Enter your email address" },
              { type: "email", message: "Enter a valid email address" },
            ]}
          >
            <Input placeholder="you@yourcompany.com" autoFocus />
          </Form.Item>
          {error && (
            <Form.Item>
              <Alert type="error" message={error} showIcon />
            </Form.Item>
          )}
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading} block>
              Continue
            </Button>
          </Form.Item>
        </Form>
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

export default function App() {
  const [realm, setRealm] = useState<string | null>(
    () => localStorage.getItem(CUSTOMER_REALM_KEY)
  );
  const [ready, setReady] = useState(false);

  // Runs when realm is first set (page load from cache or after email lookup)
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
      <EmailGate
        onRealm={(slug) => {
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
