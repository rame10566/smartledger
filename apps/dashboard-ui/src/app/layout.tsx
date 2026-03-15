import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "SmartLedger Governance Dashboard",
  description: "AutoLedger AI — Validation-Gated Immutable Ledger",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50">
        {/* Top nav */}
        <nav className="bg-blue-800 text-white shadow-md">
          <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-8">
            <span className="font-bold text-lg tracking-tight">
              SmartLedger
            </span>
            <Link
              href="/quarantine"
              className="text-blue-100 hover:text-white text-sm font-medium"
            >
              Quarantine Queue
            </Link>
            <Link
              href="/contracts"
              className="text-blue-100 hover:text-white text-sm font-medium"
            >
              Contracts
            </Link>
            <Link
              href="/reports"
              className="text-blue-100 hover:text-white text-sm font-medium"
            >
              Reports
            </Link>
          </div>
        </nav>

        {/* Page content */}
        <main className="max-w-7xl mx-auto px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
