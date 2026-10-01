import { Suspense } from "react";
import type { Metadata } from "next";
import PanDetailClient from "@/components/pans/PanDetailClient";
import { normalizePanId } from "@/lib/pans";

export async function generateMetadata(
  props: PageProps<"/pans/[panId]">,
): Promise<Metadata> {
  const { panId } = await props.params;
  return { title: normalizePanId(panId) };
}

export default async function PanDetailPage(
  props: PageProps<"/pans/[panId]">,
) {
  const { panId } = await props.params;
  return (
    <Suspense
      key={panId}
      fallback={
        <div className="mx-auto w-full max-w-5xl px-5 py-7 lg:px-8 lg:py-9">
          <p className="text-sm text-muted">Loading pan…</p>
        </div>
      }
    >
      <PanDetailClient panId={normalizePanId(panId)} />
    </Suspense>
  );
}
