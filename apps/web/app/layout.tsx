import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SensorGuard AI — Outage-Resilient Air Quality Forecasting",
  description:
    "Historical UK AURN (Q1 2024) air-quality forecast reliability showcase showing when environmental forecasts have sufficient evidence to publish vs. withhold.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-slate-950 text-slate-100 text-base min-h-screen">
        {children}
      </body>
    </html>
  );
}
