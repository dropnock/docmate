import { Button, Layout, Space, Typography } from "antd";

const { Header } = Layout;

interface Props {
  title: string;
  userName?: string;
  onSignOut: () => void;
}

/** The top app bar shared by both portals — was duplicated near-identically
 * in each portal's App.tsx. */
export default function AppHeader({ title, userName, onSignOut }: Props) {
  return (
    <Header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
        {title}
      </Typography.Title>
      <Space>
        {userName && (
          <Typography.Text style={{ color: "rgba(255,255,255,0.65)" }}>
            {userName}
          </Typography.Text>
        )}
        <Button onClick={onSignOut} type="text" style={{ color: "white" }}>
          Sign Out
        </Button>
      </Space>
    </Header>
  );
}
