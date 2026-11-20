import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentProof | Evaluations",
  description: "Run and inspect deterministic AI agent evaluations.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
