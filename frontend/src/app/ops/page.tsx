import type { Metadata } from "next";

import { Ops } from "@/components/ops/ops";

export const metadata: Metadata = { title: "Ops" };

export default function Page() {
  return <Ops />;
}
