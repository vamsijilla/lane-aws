import type { Metadata } from "next";

import { Vehicles } from "@/components/submission/vehicles";

export const metadata: Metadata = { title: "Vehicles" };

export default function Page() {
  return <Vehicles />;
}
