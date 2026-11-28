import type { ThemeConfig } from "antd";

/** DocMate brand theme, from the logo (manuals/brand/source/docmate-logo-source.svg):
 * navy primary (#092B59, "Doc"), teal for links and hover accents (#078F82,
 * "Mate"; darkened to #06776C for text so it passes WCAG AA on white), slate
 * neutrals. Inter for all UI text. Keep in step with manuals/brand/brand.yaml. */
export const docmateTheme: ThemeConfig = {
  token: {
    colorPrimary: "#092B59",
    colorPrimaryHover: "#173F75",
    colorPrimaryActive: "#061E40",
    colorPrimaryBg: "#E8EEF6",
    colorPrimaryBgHover: "#E6F4F2",
    colorLink: "#06776C",
    colorLinkHover: "#055C54",

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
      itemSelectedBg: "#092B59",
      itemSelectedColor: "#FFFFFF",
      itemHoverBg: "#E6F4F2",
      itemHoverColor: "#06776C",
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
      defaultHoverBorderColor: "#078F82",
      defaultHoverColor: "#06776C",
    },
    Card: {
      borderRadiusLG: 12,
    },
    Tag: {
      borderRadiusSM: 6,
    },
  },
};
