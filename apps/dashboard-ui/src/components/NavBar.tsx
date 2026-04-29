"use client";

import Link from "next/link";
import IdentitySelector from "./IdentitySelector";

export default function NavBar() {
  return (
    <nav className="bg-blue-800 text-white shadow-md">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-8">
        <span className="font-bold text-lg tracking-tight">SmartLedger</span>
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
          href="/conflicts"
          className="text-blue-100 hover:text-white text-sm font-medium"
        >
          Conflicts
        </Link>
        <Link
          href="/reports"
          className="text-blue-100 hover:text-white text-sm font-medium"
        >
          Reports
        </Link>
        <Link
          href="/party"
          className="text-blue-100 hover:text-white text-sm font-medium border border-blue-500 rounded px-2 py-0.5"
        >
          Party Portal
        </Link>
        <div className="ml-auto">
          <IdentitySelector />
        </div>
      </div>
    </nav>
  );
}
