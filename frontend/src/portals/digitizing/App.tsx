import { useState } from "react";
import { Layout, Menu, Typography, Button } from "antd";
import {
  DashboardOutlined,
  ProjectOutlined,
  TeamOutlined,
  WarningOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LoginPage from "@shared/components/LoginPage";
import StaffProductivityDashboard from "./pages/StaffProductivityDashboard";
import ProjectKPIDashboard from "./pages/ProjectKPIDashboard";
import StaleTaskManager from "./pages/StaleTaskManager";
import RecordHistory from "./pages/RecordHistory";
import { getStoredUser, logout } from "@shared/api/auth";
import type { AuthUser } from "@shared/types";

const { Header, Sider, Content } = Layout;
const qc = new QueryClient();

const NAV_ITEMS = [
  { key: "productivity", label: "Staff Productivity", icon: <TeamOutlined /> },
  { key: "kpi", label: "Project KPIs", icon: <ProjectOutlined /> },
  { key: "stale", label: "Stale Tasks", icon: <WarningOutlined /> },
  { key: "history", label: "Record History", icon: <HistoryOutlined /> },
];

// Demo: hard-code project 1; in production this comes from project selector
const DEMO_PROJECT_ID = 1;

function AppInner() {
  const [user, setUser] = useState<AuthUser | null>(getStoredUser);
  const [page, setPage] = useState("productivity");
  const [inspectRecordId, setInspectRecordId] = useState<number>(1);

  if (!user) return <LoginPage onLogin={setUser} portalLabel="Digitizing Entity" />;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          DocMate — Digitizing
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
        <Content style={{ padding: 24 }}>
          {page === "productivity" && <StaffProductivityDashboard projectId={DEMO_PROJECT_ID} />}
          {page === "kpi" && <ProjectKPIDashboard projectId={DEMO_PROJECT_ID} />}
          {page === "stale" && <StaleTaskManager projectId={DEMO_PROJECT_ID} />}
          {page === "history" && <RecordHistory recordId={inspectRecordId} />}
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
