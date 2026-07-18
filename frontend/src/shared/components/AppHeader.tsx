import { Button, Layout, Space, Typography } from "antd";
import { LogOut, Menu as MenuIcon, X } from "lucide-react";

const { Header } = Layout;

// Baked in at Docker build time (see RELEASING.md) — "dev"/"unknown" outside
// a Docker build (e.g. plain `vite dev`). Cast via `any` like VITE_KEYCLOAK_URL
// in keycloak.ts — vite-env.d.ts's ImportMetaEnv only declares VITE_PORTAL.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const APP_VERSION = ((import.meta as any).env?.VITE_APP_VERSION as string | undefined) ?? "dev";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const APP_GIT_COMMIT = ((import.meta as any).env?.VITE_GIT_COMMIT as string | undefined) ?? "unknown";

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
        <Typography.Text
          title={`commit ${APP_GIT_COMMIT}`}
          style={{ color: "#94A3B8", fontSize: 11 }}
        >
          v{APP_VERSION}
        </Typography.Text>
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
