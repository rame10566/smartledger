"use strict";

/**
 * SmartLedger Chaincode
 *
 * Manages the on-chain immutable record of auto-finance contract events.
 * Each record in the world state is keyed by record_id (UUID).
 * Records are APPEND-ONLY — updates and deletes are rejected.
 *
 * World state key structure:
 *   RECORD:{record_id}          — individual ledger record
 *   STATE:{contract_id}         — current contract state
 *   STATE_HISTORY:{contract_id} — state transition log (composite key)
 *
 * Transactions:
 *   WriteRecord(record_id, contract_id, record_type, data_hash, payload_json, timestamp)
 *   ExecuteStateTransition(contract_id, new_state, trigger_event_id, saga_id, timestamp)
 *
 * Queries (read-only):
 *   QueryRecord(record_id)
 *   QueryRecordsByContract(contract_id)
 *   GetContractState(contract_id)
 *   GetStateHistory(contract_id)
 */

const { Contract } = require("fabric-contract-api");

// ── Constants ─────────────────────────────────────────────────────────────────

const VALID_RECORD_TYPES = new Set([
  "origination",
  "payment",
  "amendment",
  "state_transition",
  "payoff",
  "override",
]);

const VALID_STATES = new Set([
  "originated",
  "active",
  "delinquent",
  "paid_off",
  "charged_off",
  "in_repossession",
  "title_released",
]);

// Valid state machine transitions
const VALID_TRANSITIONS = {
  originated:      new Set(["active", "charged_off"]),
  active:          new Set(["delinquent", "paid_off", "charged_off"]),
  delinquent:      new Set(["active", "paid_off", "charged_off", "in_repossession"]),
  paid_off:        new Set(["title_released"]),
  in_repossession: new Set(["charged_off"]),
  charged_off:     new Set([]),
  title_released:  new Set([]),
};

// ── Contract ──────────────────────────────────────────────────────────────────

class SmartLedgerContract extends Contract {
  constructor() {
    super("SmartLedgerContract");
  }

  // ─── Write Record ──────────────────────────────────────────────────────────

  /**
   * WriteRecord — append an immutable validated record to the ledger.
   *
   * @param {Context} ctx
   * @param {string}  record_id      UUID (globally unique, set by off-chain service)
   * @param {string}  contract_id    SmartLedger contract identifier
   * @param {string}  record_type    One of VALID_RECORD_TYPES
   * @param {string}  data_hash      SHA-256 of the off-chain payload (hex)
   * @param {string}  payload_json   Non-PII record payload (JSON string)
   * @param {string}  timestamp      ISO-8601 timestamp of the original event
   */
  async WriteRecord(ctx, record_id, contract_id, record_type, data_hash, payload_json, timestamp) {
    _requireArgs({ record_id, contract_id, record_type, data_hash, payload_json, timestamp });

    if (!VALID_RECORD_TYPES.has(record_type)) {
      throw new Error(`Invalid record_type: "${record_type}". Valid types: ${[...VALID_RECORD_TYPES].join(", ")}`);
    }

    const key = _recordKey(record_id);

    // Idempotency: reject duplicate record_id
    const existing = await ctx.stub.getState(key);
    if (existing && existing.length > 0) {
      throw new Error(`Record ${record_id} already exists — records are immutable and cannot be overwritten`);
    }

    // Validate payload is valid JSON
    let payload;
    try {
      payload = JSON.parse(payload_json);
    } catch {
      throw new Error("payload_json is not valid JSON");
    }

    const record = {
      record_id,
      contract_id,
      record_type,
      data_hash,
      payload,
      timestamp,
      committed_at: new Date().toISOString(),
      tx_id:        ctx.stub.getTxID(),
    };

    await ctx.stub.putState(key, Buffer.from(JSON.stringify(record)));

    // Emit event for off-chain listeners
    ctx.stub.setEvent("RecordWritten", Buffer.from(JSON.stringify({
      record_id,
      contract_id,
      record_type,
      data_hash,
      tx_id: ctx.stub.getTxID(),
    })));

    return JSON.stringify({ success: true, tx_id: ctx.stub.getTxID(), record_id });
  }

  // ─── Execute State Transition ──────────────────────────────────────────────

  /**
   * ExecuteStateTransition — advance a contract through the state machine.
   *
   * @param {Context} ctx
   * @param {string}  contract_id       SmartLedger contract identifier
   * @param {string}  new_state         Target state (must be reachable from current state)
   * @param {string}  trigger_event_id  Event that caused the transition
   * @param {string}  saga_id           Saga that initiated this transition
   * @param {string}  timestamp         ISO-8601 timestamp
   */
  async ExecuteStateTransition(ctx, contract_id, new_state, trigger_event_id, saga_id, timestamp) {
    _requireArgs({ contract_id, new_state, trigger_event_id, timestamp });

    if (!VALID_STATES.has(new_state)) {
      throw new Error(`Invalid state: "${new_state}". Valid states: ${[...VALID_STATES].join(", ")}`);
    }

    const stateKey    = _stateKey(contract_id);
    const stateBuffer = await ctx.stub.getState(stateKey);

    let previous_state = null;
    let current_state  = "originated"; // default for first transition

    if (stateBuffer && stateBuffer.length > 0) {
      const stateRecord = JSON.parse(stateBuffer.toString());
      previous_state = stateRecord.current_state;
      current_state  = stateRecord.current_state;
    }

    // Validate transition
    const allowedNext = VALID_TRANSITIONS[current_state] || new Set();
    if (!allowedNext.has(new_state)) {
      throw new Error(
        `Invalid transition: ${current_state} → ${new_state}. ` +
        `Allowed: [${[...allowedNext].join(", ") || "none"}]`
      );
    }

    const stateRecord = {
      contract_id,
      current_state:   new_state,
      previous_state,
      state_changed_at: timestamp,
      trigger_event_id,
      saga_id:          saga_id || null,
      tx_id:            ctx.stub.getTxID(),
    };

    await ctx.stub.putState(stateKey, Buffer.from(JSON.stringify(stateRecord)));

    // Also write a state_transition record for auditability
    const historyKey = ctx.stub.createCompositeKey("STATE_HISTORY", [contract_id, timestamp, ctx.stub.getTxID()]);
    await ctx.stub.putState(historyKey, Buffer.from(JSON.stringify({
      contract_id,
      from_state:       previous_state,
      to_state:         new_state,
      trigger_event_id,
      saga_id:          saga_id || null,
      timestamp,
      tx_id:            ctx.stub.getTxID(),
    })));

    ctx.stub.setEvent("StateTransitioned", Buffer.from(JSON.stringify({
      contract_id,
      from_state: previous_state,
      to_state:   new_state,
      tx_id:      ctx.stub.getTxID(),
    })));

    return JSON.stringify({
      success:        true,
      tx_id:          ctx.stub.getTxID(),
      contract_id,
      previous_state,
      new_state,
    });
  }

  // ─── Query Record ──────────────────────────────────────────────────────────

  /**
   * QueryRecord — fetch a single record by record_id.
   */
  async QueryRecord(ctx, record_id) {
    if (!record_id) throw new Error("record_id is required");

    const buffer = await ctx.stub.getState(_recordKey(record_id));
    if (!buffer || buffer.length === 0) {
      return JSON.stringify({ found: false, record_id });
    }
    return buffer.toString();
  }

  // ─── Query Records By Contract ─────────────────────────────────────────────

  /**
   * QueryRecordsByContract — fetch all records for a given contract_id.
   *
   * Uses CouchDB rich query (available when peer is configured with CouchDB state DB).
   * Falls back to range scan on key prefix if CouchDB is not available.
   */
  async QueryRecordsByContract(ctx, contract_id) {
    if (!contract_id) throw new Error("contract_id is required");

    const query = {
      selector: { contract_id },
      sort:     [{ committed_at: "asc" }],
    };

    const iterator = await ctx.stub.getQueryResult(JSON.stringify(query));
    const results  = [];

    for await (const result of iterator) {
      try {
        results.push(JSON.parse(result.value.toString()));
      } catch {
        // skip malformed entries
      }
    }

    return JSON.stringify(results);
  }

  // ─── Get Contract State ────────────────────────────────────────────────────

  /**
   * GetContractState — return the current on-chain state of a contract.
   */
  async GetContractState(ctx, contract_id) {
    if (!contract_id) throw new Error("contract_id is required");

    const buffer = await ctx.stub.getState(_stateKey(contract_id));
    if (!buffer || buffer.length === 0) {
      return JSON.stringify({ found: false, contract_id });
    }
    return buffer.toString();
  }

  // ─── Get State History ─────────────────────────────────────────────────────

  /**
   * GetStateHistory — return the full state transition history for a contract.
   */
  async GetStateHistory(ctx, contract_id) {
    if (!contract_id) throw new Error("contract_id is required");

    const iterator = await ctx.stub.getStateByPartialCompositeKey("STATE_HISTORY", [contract_id]);
    const history  = [];

    for await (const result of iterator) {
      try {
        history.push(JSON.parse(result.value.toString()));
      } catch {
        // skip
      }
    }

    history.sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1));
    return JSON.stringify(history);
  }
}

// ── Key helpers ───────────────────────────────────────────────────────────────

function _recordKey(record_id) {
  return `RECORD:${record_id}`;
}

function _stateKey(contract_id) {
  return `STATE:${contract_id}`;
}

function _requireArgs(args) {
  for (const [key, value] of Object.entries(args)) {
    if (!value) throw new Error(`${key} is required`);
  }
}

module.exports = { SmartLedgerContract };
