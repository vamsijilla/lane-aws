import type { Metadata } from "next";

import { Rules } from "@/components/submission/rules";

export const metadata: Metadata = { title: "Rules" };

export default function Page() {
  return <Rules />;
}
