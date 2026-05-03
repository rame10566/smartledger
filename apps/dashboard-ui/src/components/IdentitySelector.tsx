"use client";

import { useEffect, useState, useCallback } from "react";
import {
  DEMO_IDENTITIES,
  getCurrentIdentity,
  setCurrentIdentity,
  type Identity,
} from "@/lib/api";

/**
 * Identity Selector — Smart Data Gateway (Section 6.5)
 *
 * POC demo dropdown that lets the user switch between pre-configured
 * identities (admin, auditor, operator, compliance, borrower, dealer).
 * The selected identity is sent as X-SmartLedger-Identity on every API call.
 */
export default function IdentitySelector() {
  const [selected, setSelected] = useState<string>(getCurrentIdentity().actor_id);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    const identity = DEMO_IDENTITIES.find((i) => i.actor_id === selected);
    if (identity) {
      setCurrentIdentity(identity);
    }
  }, [selected]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setSwitching(true);
    setSelected(e.target.value);
    // Brief fade-out before reload so the transition feels intentional
    document.body.style.opacity = "0.5";
    document.body.style.transition = "opacity 150ms ease-out";
    setTimeout(() => window.location.reload(), 150);
  }, []);

  const current = DEMO_IDENTITIES.find((i) => i.actor_id === selected);
  const isParty = current && !current.role;

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="identity-select" className="text-xs text-blue-200 whitespace-nowrap">
        Viewing as:
      </label>
      <select
        id="identity-select"
        value={selected}
        onChange={handleChange}
        disabled={switching}
        className="text-xs bg-blue-700 text-white border border-blue-600 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:opacity-50"
      >
        <optgroup label="Operational Roles">
          {DEMO_IDENTITIES.filter((i) => i.role).map((i) => (
            <option key={i.actor_id} value={i.actor_id}>
              {i.label}
            </option>
          ))}
        </optgroup>
        <optgroup label="Contract Parties">
          {DEMO_IDENTITIES.filter((i) => !i.role).map((i) => (
            <option key={i.actor_id} value={i.actor_id}>
              {i.label}
            </option>
          ))}
        </optgroup>
      </select>
      {switching && (
        <span className="text-xs text-blue-200 animate-pulse">Switching...</span>
      )}
      {!switching && isParty && (
        <span className="text-xs text-yellow-300">Party view</span>
      )}
    </div>
  );
}
