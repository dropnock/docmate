import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import App from "./App";
import { docmateTheme } from "@shared/theme/theme";
import "@shared/styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider theme={docmateTheme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
