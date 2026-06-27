import { useState } from "react";
import { Layout, Menu, Typography, Button } from "antd";
import { ProjectOutlined, CheckCircleOutlined, HistoryOutlined } from "@ant-design/icons";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LoginPage from "@shared/components/LoginPage";
import ProjectKPIDashboard from "../digitizing/pages/ProjectKPIDashboard";
import RecordHistory from "../digitizing/pages/RecordHistory";
import QCWorkspace from "./pages/QCWorkspace";
import { getStoredUser, logout } from "@shared/api/auth";
import type { AuthUser } from "@shared/types";

const { Header, Sider, Content } = Layout;
const queryClient = new QueryClient();

const NAV_ITEMS = [
  { key: "qc", label: "QC Workspace", icon: <CheckCircleOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

const DEMO_PROJECT_ID = 1;

function AppInner() {
  const [user, setUser] = useState<AuthUser | null>(getStoredUser);
  const [page, setPage] = useState("qc");

  if (!user) return <LoginPage onLogin={setUser} portalLabel="Customer" />;

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
          {page === "qc" && <QCWorkspace />}
          {page === "kpi" && <ProjectKPIDashboard projectId={DEMO_PROJECT_ID} />}
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
