import type { Metadata } from "next";

import { CoverageLosses } from "@/components/submission/coverage";

export const metadata: Metadata = { title: "Coverage and losses" };

export default function Page() {
  return <CoverageLosses />;
}
