import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TCG Pipeline Tracker",
  description: "Research workflow and evidence review for development pipeline tracking."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
