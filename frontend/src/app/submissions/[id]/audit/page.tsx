import type { Metadata } from "next";

import { Audit } from "@/components/submission/audit";

export const metadata: Metadata = { title: "Audit" };

export default function Page() {
  return <Audit />;
}
