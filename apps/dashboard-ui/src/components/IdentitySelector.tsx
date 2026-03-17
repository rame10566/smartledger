"use client";

import { useEffect, useState } from "react";
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

  useEffect(() => {
    const identity = DEMO_IDENTITIES.find((i) => i.actor_id === selected);
    if (identity) {
      setCurrentIdentity(identity);
    }
  }, [selected]);

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
        onChange={(e) => {
          setSelected(e.target.value);
          // Force page reload so data re-fetches with new identity
          window.location.reload();
        }}
        className="text-xs bg-blue-700 text-white border border-blue-600 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400"
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
      {isParty && (
        <span className="text-xs text-yellow-300">Party view</span>
      )}
    </div>
  );
}
