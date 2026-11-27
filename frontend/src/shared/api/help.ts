import api from "./client";

export interface HelpManual {
  slug: string;
  title: string;
  kind: "manual" | "workbook";
  /** Absolute path of the built HTML page (no sign-in needed). */
  html_url: string;
  /** API path of the PDF prepared for the user's organisation. */
  pdf_url: string;
}

export interface HelpManualList {
  version: string;
  manuals: HelpManual[];
}

export const helpManualsKey = ["help-manuals"] as const;

export function listHelpManuals(): Promise<HelpManualList> {
  return api.get<HelpManualList>("/help/manuals").then((r) => r.data);
}

/** Downloads the manual as a PDF stamped with the user's organisation. */
export async function downloadHelpManualPdf(manual: HelpManual) {
  const res = await api.get<Blob>(`/help/manuals/${manual.slug}.pdf`, { responseType: "blob" });
  const match = /filename="?([^";]+)"?/.exec(res.headers["content-disposition"] ?? "");
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? `${manual.slug}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
