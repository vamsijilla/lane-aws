import type { Metadata } from "next";

import { OverviewBrief } from "@/components/submission/overview";

export const metadata: Metadata = { title: "Brief" };

export default function Page() {
  return <OverviewBrief />;
}
