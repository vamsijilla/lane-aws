import type { Metadata } from "next";

import { Missing } from "@/components/submission/missing";

export const metadata: Metadata = { title: "Missing" };

export default function Page() {
  return <Missing />;
}
