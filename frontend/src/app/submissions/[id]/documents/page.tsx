import type { Metadata } from "next";

import { Documents } from "@/components/submission/documents";

export const metadata: Metadata = { title: "Documents" };

export default function Page() {
  return <Documents />;
}
