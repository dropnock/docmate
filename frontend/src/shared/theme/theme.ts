import type { ThemeConfig } from "antd";

/** DocMate brand theme — a professional indigo instead of antd's stock blue,
 * a deep-navy header to pair with it, system font stack (no external font
 * fetch — this is an internal tool, not worth a network round trip for a
 * marginal typography gain). */
export const docmateTheme: ThemeConfig = {
  token: {
    colorPrimary: "#155EEF",
    borderRadius: 6,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  },
  components: {
    Layout: {
      headerBg: "#0B1B33",
    },
    Menu: {
      itemSelectedBg: "rgba(21,94,239,0.1)",
    },
  },
};
