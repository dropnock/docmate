import { Button, Layout, Space, Typography } from "antd";
import { LogOut, Menu as MenuIcon, X } from "lucide-react";

const { Header } = Layout;

interface Props {
  portalLabel: string;
  userName?: string;
  onSignOut: () => void;
  mobileNavOpen?: boolean;
  onToggleMobileNav?: () => void;
}

/** The top app bar shared by both portals — was duplicated near-identically
 * in each portal's App.tsx. */
export default function AppHeader({
  portalLabel,
  userName,
  onSignOut,
  mobileNavOpen,
  onToggleMobileNav,
}: Props) {
  return (
    <Header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "#FFFFFF",
        borderBottom: "1px solid #E2E8F0",
        padding: "0 24px",
      }}
    >
      <Space size={12} align="center">
        {onToggleMobileNav && (
          <button
            type="button"
            aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"}
            onClick={onToggleMobileNav}
            className="docmate-mobile-nav-toggle"
            style={{
              alignItems: "center",
              justifyContent: "center",
              width: 36,
              height: 36,
              border: "1px solid #E2E8F0",
              borderRadius: 8,
              background: "#FFFFFF",
              cursor: "pointer",
            }}
          >
            {mobileNavOpen ? (
              <X size={18} color="#0F172A" />
            ) : (
              <MenuIcon size={18} color="#0F172A" />
            )}
          </button>
        )}
        <Typography.Title level={4} style={{ margin: 0, lineHeight: "normal" }}>
          <span style={{ color: "#1E40AF", fontWeight: 700 }}>DocMate</span>{" "}
          <span style={{ color: "#64748B", fontWeight: 500, fontSize: 15 }}>
            {portalLabel}
          </span>
        </Typography.Title>
      </Space>
      <Space size={16}>
        {userName && (
          <Typography.Text className="docmate-header-username" style={{ color: "#64748B" }}>
            {userName}
          </Typography.Text>
        )}
        <Button onClick={onSignOut} type="text" icon={<LogOut size={16} />}>
          Sign Out
        </Button>
      </Space>
    </Header>
  );
}
