"""
Hyperledger Fabric Client — peer CLI subprocess bridge.

Wraps the Fabric peer CLI (installed by setup-fabric.sh) to submit and query
chaincode transactions.  This approach is used because the official Fabric
Gateway Python SDK is not yet published to PyPI.  The peer binary is used
directly; all identity / TLS material is read from the filesystem paths
configured in .env.

Configuration (via Settings / .env):
  FABRIC_PEER_ENDPOINT      e.g. "localhost:7051"
  FABRIC_ORDERER_ENDPOINT   e.g. "localhost:7050"
  FABRIC_CHANNEL            e.g. "smartledger-channel"
  FABRIC_CHAINCODE          e.g. "smartledger-cc"
  FABRIC_MSP_ID             e.g. "SmartLedgerOrgMSP"
  FABRIC_CERT_PATH          Path to PEM admin certificate (for CORE_PEER_TLS_CERT_FILE)
  FABRIC_KEY_PATH           Path to PEM private key  (not used directly; MSP dir sets this)
  FABRIC_TLS_CERT_PATH      Path to peer TLS root cert PEM
  FABRIC_ORDERER_TLS_PATH   Path to orderer TLS root cert PEM
  FABRIC_ADMIN_MSP_PATH     Path to admin MSP directory
  FABRIC_CFG_PATH           Path to Fabric config dir (contains core.yaml)
  FABRIC_BIN_PATH           Path to Fabric bin dir (contains peer binary)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


class FabricError(Exception):
    """Raised when a Fabric transaction or query fails."""


class FabricClient:
    """
    Async wrapper around the peer CLI for chaincode submit/query.

    submit_transaction() → peer chaincode invoke  (requires ordering)
    evaluate_transaction() → peer chaincode query  (read-only)

    Both methods run the peer binary in a thread pool so they don't block
    the asyncio event loop.
    """

    def __init__(
        self,
        peer_endpoint:     str,
        orderer_endpoint:  str,
        channel:           str,
        chaincode:         str,
        msp_id:            str,
        tls_cert_path:     str,   # peer TLS root cert
        orderer_tls_path:  str,   # orderer TLS root cert
        admin_msp_path:    str,   # admin MSP directory (contains signcerts + keystore)
        fabric_cfg_path:   str,   # dir containing core.yaml
        fabric_bin_path:   str,   # dir containing peer binary
    ) -> None:
        self._peer_endpoint    = peer_endpoint
        self._orderer_endpoint = orderer_endpoint
        self._channel          = channel
        self._chaincode        = chaincode
        self._msp_id           = msp_id
        self._tls_cert_path    = tls_cert_path
        self._orderer_tls_path = orderer_tls_path
        self._admin_msp_path   = admin_msp_path
        self._fabric_cfg_path  = fabric_cfg_path
        self._fabric_bin_path  = fabric_bin_path

    # ── Lifecycle (no-ops for CLI bridge) ──────────────────────────────────────

    async def connect(self) -> None:
        """Validate that the peer binary and crypto material are accessible."""
        peer_bin = Path(self._fabric_bin_path) / "peer"
        if not peer_bin.exists():
            raise RuntimeError(
                f"Fabric peer binary not found at {peer_bin}. "
                "Run infra/fabric/scripts/setup-fabric.sh first."
            )
        for label, path in [
            ("TLS cert",         self._tls_cert_path),
            ("orderer TLS cert", self._orderer_tls_path),
            ("admin MSP",        self._admin_msp_path),
            ("Fabric config",    self._fabric_cfg_path),
        ]:
            if not Path(path).exists():
                raise RuntimeError(
                    f"Fabric {label} not found at {path}. "
                    "Run infra/fabric/scripts/setup-fabric.sh first."
                )
        logger.info(
            "fabric_client_ready",
            peer=self._peer_endpoint,
            channel=self._channel,
            chaincode=self._chaincode,
        )

    async def close(self) -> None:
        """Nothing to close for the CLI bridge."""

    # ── Public API ─────────────────────────────────────────────────────────────

    async def write_record(
        self,
        record_id:   str,
        contract_id: str,
        record_type: str,
        data_hash:   str,
        payload:     dict[str, Any],
        timestamp:   str,
    ) -> str:
        """Submit a WriteRecord transaction.  Returns the Fabric tx_id."""
        payload_json = json.dumps(payload)
        result_bytes = await self._submit_transaction(
            "WriteRecord",
            record_id,
            contract_id,
            record_type,
            data_hash,
            payload_json,
            timestamp,
        )
        result = json.loads(result_bytes)
        tx_id = result.get("tx_id", "")
        logger.info(
            "fabric_record_written",
            record_id=record_id,
            contract_id=contract_id,
            record_type=record_type,
            tx_id=tx_id,
        )
        return tx_id

    async def execute_state_transition(
        self,
        contract_id:      str,
        new_state:        str,
        trigger_event_id: str,
        saga_id:          str,
        timestamp:        str,
    ) -> str:
        """Submit an ExecuteStateTransition transaction.  Returns the Fabric tx_id."""
        result_bytes = await self._submit_transaction(
            "ExecuteStateTransition",
            contract_id,
            new_state,
            trigger_event_id,
            saga_id or "",
            timestamp,
        )
        result = json.loads(result_bytes)
        tx_id  = result.get("tx_id", "")
        logger.info(
            "fabric_state_transition",
            contract_id=contract_id,
            new_state=new_state,
            tx_id=tx_id,
        )
        return tx_id

    async def query_record(self, record_id: str) -> dict[str, Any]:
        """Query a single record (read-only).  Returns the record dict."""
        result_bytes = await self._evaluate_transaction("QueryRecord", record_id)
        return json.loads(result_bytes)

    async def query_records_by_contract(self, contract_id: str) -> list[dict[str, Any]]:
        """Query all records for a contract (CouchDB rich query)."""
        result_bytes = await self._evaluate_transaction(
            "QueryRecordsByContract", contract_id
        )
        return json.loads(result_bytes)

    async def get_contract_state(self, contract_id: str) -> dict[str, Any]:
        """Get the current on-chain state of a contract."""
        result_bytes = await self._evaluate_transaction("GetContractState", contract_id)
        return json.loads(result_bytes)

    async def get_state_history(self, contract_id: str) -> list[dict[str, Any]]:
        """Get the full state transition history for a contract."""
        result_bytes = await self._evaluate_transaction("GetStateHistory", contract_id)
        return json.loads(result_bytes)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _peer_env(self) -> dict[str, str]:
        """Build the environment variables required by the peer CLI."""
        env = os.environ.copy()
        env.update({
            "FABRIC_CFG_PATH":              self._fabric_cfg_path,
            "CORE_PEER_TLS_ENABLED":        "true",
            "CORE_PEER_LOCALMSPID":         self._msp_id,
            "CORE_PEER_MSPCONFIGPATH":      self._admin_msp_path,
            "CORE_PEER_ADDRESS":            self._peer_endpoint,
            "CORE_PEER_TLS_ROOTCERT_FILE":  self._tls_cert_path,
            # Prepend Fabric bin to PATH so 'peer' resolves correctly
            "PATH": f"{self._fabric_bin_path}:{env.get('PATH', '')}",
        })
        return env

    def _ctor(self, fn: str, args: list[str]) -> str:
        """Build the --ctor JSON string for the peer CLI."""
        return json.dumps({"function": fn, "Args": args})

    def _run_invoke(self, fn: str, args: list[str]) -> str:
        """Blocking: run `peer chaincode invoke` and return the chaincode payload."""
        cmd = [
            "peer", "chaincode", "invoke",
            "-o", self._orderer_endpoint,
            "--channelID", self._channel,
            "--name", self._chaincode,
            "--ctor", self._ctor(fn, args),
            "--tls",
            "--cafile", self._orderer_tls_path,
            "--peerAddresses", self._peer_endpoint,
            "--tlsRootCertFiles", self._tls_cert_path,
            "--waitForEvent",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=self._peer_env(),
            timeout=60,
        )

        # peer invoke writes INFO logs to stderr; the chaincode payload is in
        # the last INFO log line: "Chaincode invoke successful. result: status:200 payload:..."
        combined = result.stderr + result.stdout
        logger.debug("peer_invoke_output", fn=fn, output=combined[-500:])

        if result.returncode != 0:
            raise FabricError(
                f"peer chaincode invoke failed (rc={result.returncode}): {combined[-400:]}"
            )

        # Extract the chaincode payload from the last matching log line.
        # The peer CLI wraps the JSON payload in double quotes and escapes
        # internal double quotes as \" — e.g.:
        #   payload:"{\"success\":true,\"tx_id\":\"...\"}"
        # We strip the outer quotes and unescape \" → " to get valid JSON.
        for line in reversed(combined.splitlines()):
            if "Chaincode invoke successful" in line and 'payload:"' in line:
                payload_start = line.index('payload:"') + len('payload:"')
                payload_end   = line.rindex('"')
                raw = line[payload_start:payload_end]
                # Unescape peer-CLI shell quoting: \" → "
                return raw.replace('\\"', '"')

        # If no payload line (e.g. chaincode returned empty), return empty JSON
        return "{}"

    def _run_query(self, fn: str, args: list[str]) -> str:
        """Blocking: run `peer chaincode query` and return the chaincode response."""
        cmd = [
            "peer", "chaincode", "query",
            "-C", self._channel,
            "-n", self._chaincode,
            "--ctor", self._ctor(fn, args),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=self._peer_env(),
            timeout=30,
        )
        if result.returncode != 0:
            raise FabricError(
                f"peer chaincode query failed (rc={result.returncode}): "
                f"{result.stderr[-400:]}"
            )
        return result.stdout.strip()

    async def _submit_transaction(self, fn: str, *args: str) -> str:
        """Async wrapper: run invoke in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._run_invoke, fn, list(args)
        )

    async def _evaluate_transaction(self, fn: str, *args: str) -> str:
        """Async wrapper: run query in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._run_query, fn, list(args)
        )


# ── Factory ────────────────────────────────────────────────────────────────────

def create_fabric_client_from_settings(settings) -> "FabricClient | None":
    """
    Create a FabricClient from Settings, or return None if Fabric is not configured.

    Returns None if any required config is missing — the Ledger MCP will then
    stay in PostgreSQL-only (write-guard) mode.
    """
    peer_endpoint = getattr(settings, "fabric_peer_endpoint", "")
    msp_id        = getattr(settings, "fabric_msp_id", "")

    if not peer_endpoint or not msp_id:
        logger.warning(
            "fabric_not_configured",
            peer_endpoint=peer_endpoint,
            msp_id=msp_id,
            hint="Set FABRIC_PEER_ENDPOINT and FABRIC_MSP_ID to enable live Fabric writes",
        )
        return None

    orderer_endpoint = getattr(settings, "fabric_orderer_endpoint", "localhost:7050")
    channel          = getattr(settings, "fabric_channel",           "smartledger-channel")
    chaincode        = getattr(settings, "fabric_chaincode",         "smartledger-cc")
    tls_cert_path    = getattr(settings, "fabric_tls_cert_path",     "")
    orderer_tls_path = getattr(settings, "fabric_orderer_tls_path",  "")
    admin_msp_path   = getattr(settings, "fabric_admin_msp_path",    "")
    fabric_cfg_path  = getattr(settings, "fabric_cfg_path",          "")
    fabric_bin_path  = getattr(settings, "fabric_bin_path",          "")

    required = {
        "FABRIC_TLS_CERT_PATH":   tls_cert_path,
        "FABRIC_ORDERER_TLS_PATH": orderer_tls_path,
        "FABRIC_ADMIN_MSP_PATH":  admin_msp_path,
        "FABRIC_CFG_PATH":        fabric_cfg_path,
        "FABRIC_BIN_PATH":        fabric_bin_path,
    }
    for var, val in required.items():
        if not val or not Path(val).exists():
            logger.warning(
                "fabric_path_not_found",
                variable=var,
                path=val,
                hint=f"Set {var} in .env and run infra/fabric/scripts/setup-fabric.sh",
            )
            return None

    return FabricClient(
        peer_endpoint=peer_endpoint,
        orderer_endpoint=orderer_endpoint,
        channel=channel,
        chaincode=chaincode,
        msp_id=msp_id,
        tls_cert_path=tls_cert_path,
        orderer_tls_path=orderer_tls_path,
        admin_msp_path=admin_msp_path,
        fabric_cfg_path=fabric_cfg_path,
        fabric_bin_path=fabric_bin_path,
    )
