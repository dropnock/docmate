import type { ThemeConfig } from "antd";

/** DocMate brand theme — single primary (deep blue) + slate neutral scale,
 * no other brand colors. Inter for all UI text. */
export const docmateTheme: ThemeConfig = {
  token: {
    colorPrimary: "#1E40AF",
    colorPrimaryHover: "#1E3A8A",
    colorPrimaryActive: "#1E3A8A",
    colorPrimaryBg: "#EFF6FF",
    colorPrimaryBgHover: "#EFF6FF",
    colorLink: "#1E40AF",
    colorLinkHover: "#1E3A8A",

    colorText: "#0F172A",
    colorTextSecondary: "#64748B",
    colorTextTertiary: "#64748B",
    colorBorder: "#E2E8F0",
    colorBorderSecondary: "#E2E8F0",
    colorBgLayout: "#F8FAFC",
    colorBgContainer: "#FFFFFF",
    colorBgBase: "#FFFFFF",

    borderRadius: 8,
    borderRadiusLG: 12,

    fontFamily:
      "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  },
  components: {
    Layout: {
      headerBg: "#FFFFFF",
      siderBg: "#FFFFFF",
      bodyBg: "#F8FAFC",
    },
    Menu: {
      itemSelectedBg: "#1E40AF",
      itemSelectedColor: "#FFFFFF",
      itemHoverBg: "#EFF6FF",
      itemHoverColor: "#1E40AF",
      itemColor: "#0F172A",
      itemBorderRadius: 8,
      itemMarginBlock: 4,
      itemMarginInline: 8,
      itemPaddingInline: 12,
      groupTitleColor: "#94A3B8",
      groupTitleFontSize: 12,
      activeBarBorderWidth: 0,
    },
    Table: {
      cellPaddingBlock: 20,
      cellPaddingInline: 24,
      headerBg: "#FFFFFF",
      headerColor: "#64748B",
      borderColor: "#E2E8F0",
    },
    Button: {
      borderRadius: 8,
      primaryShadow: "none",
      defaultHoverBorderColor: "#1E40AF",
      defaultHoverColor: "#1E40AF",
    },
    Card: {
      borderRadiusLG: 12,
    },
    Tag: {
      borderRadiusSM: 6,
    },
  },
};
