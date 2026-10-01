import type { Metadata } from "next";
import EditPanClient from "@/components/pans/EditPanClient";
import { normalizePanId } from "@/lib/pans";

export const metadata: Metadata = {
  title: "Edit pan",
};

export default async function EditPanPage(props: PageProps<"/pans/[panId]/edit">) {
  const { panId } = await props.params;
  return <EditPanClient panId={normalizePanId(panId)} />;
}
