import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SensorGuard AI — Private Staging Dashboard",
  description: "Private staging evaluation interface for SensorGuard AI Q1 2024 UK AURN Pilot Network (8 stations).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-slate-950 text-slate-100 min-h-screen">
        {children}
      </body>
    </html>
  );
}
