import type { Metadata } from "next";

import { NextActions } from "@/components/submission/actions";

export const metadata: Metadata = { title: "Next actions" };

export default function Page() {
  return <NextActions />;
}
