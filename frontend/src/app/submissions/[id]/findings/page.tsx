import type { Metadata } from "next";

import { Findings } from "@/components/submission/findings";

export const metadata: Metadata = { title: "AI findings" };

export default function Page() {
  return <Findings />;
}
