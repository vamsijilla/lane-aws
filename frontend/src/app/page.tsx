import type { Metadata } from "next";

import { Inbox } from "@/components/inbox/inbox";

export const metadata: Metadata = { title: "Inbox" };

export default function InboxPage() {
  return (
    <div className="px-8 py-6">
      <Inbox />
    </div>
  );
}
