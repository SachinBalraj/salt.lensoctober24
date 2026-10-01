import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Salt pans",
  description:
    "Register, inspect, edit and deactivate salt-pan production units.",
};

export default function PansLayout({ children }: LayoutProps<"/pans">) {
  return <>{children}</>;
}
