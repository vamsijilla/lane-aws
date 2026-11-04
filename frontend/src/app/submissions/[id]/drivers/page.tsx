import type { Metadata } from "next";

import { Drivers } from "@/components/submission/drivers";

export const metadata: Metadata = { title: "Drivers" };

export default function Page() {
  return <Drivers />;
}
