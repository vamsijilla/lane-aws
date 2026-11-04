import type { Metadata } from "next";

import { Conflicts } from "@/components/submission/conflicts";

export const metadata: Metadata = { title: "Conflicts" };

export default function Page() {
  return <Conflicts />;
}
