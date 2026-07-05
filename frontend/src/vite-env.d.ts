/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PORTAL: "digitizing" | "customer";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
