# SmartLedger POC — Known Limitations

This document captures intentional simplifications in the POC that would
need to be addressed for any production deployment.

## Hyperledger Fabric

- **Single Raft consenter** (1 orderer node) — no fault tolerance.
  Production: minimum 3 orderers across availability zones.
- **Single peer organization** (`SmartLedgerOrg`) — endorsement is
  trivially satisfied by SmartLedger itself; no actual multi-party trust.
  Production: multiple peer orgs with explicit endorsement policies.
- **Self-signed crypto material** generated via `cryptogen`.
  Production: Fabric CA with proper PKI hierarchy and rotation.
- **Default CouchDB credentials** (`admin`/`adminpw`) — POC only.
  Production: secret-managed credentials, network isolation.
- **No HSM-backed signing** — peer/orderer keys live in the filesystem.
  Production: HSM or KMS-backed key management.
- **No channel access control policies** beyond the default implicit
  policies. Production: explicit application-level ACLs per channel.
- **No private data collections** — all on-chain data is visible to all
  org members. Production: private data for PII/sensitive fields.

## Application

- **JWT secrets** are environment-variable driven with placeholder defaults
  that fail-fast in non-test environments. Production: secret manager
  (AWS Secrets Manager, HashiCorp Vault, etc.) with rotation policies.
- **No rate limiting** on the Dashboard API or MCP endpoints.
- **No authentication on simulated source MCPs** — POC assumption that
  all source systems are trusted internal callers.
- **No TLS between internal services** — all MCP and API traffic is
  plaintext HTTP on localhost/Docker network. Production: mTLS everywhere.
- **Proof token shared secret** (HS256) — both Validation Engine and
  Ledger MCP share the same symmetric key. Production: asymmetric signing
  (RS256/ES256) with separate key pairs.
- **No PII encryption at rest** — Postgres stores customer data in
  plaintext. Production: column-level encryption or Vault transit engine.

## Operational

- **No monitoring/observability** wired in (metrics, traces, logs are
  structured JSON but not shipped to any collector).
- **No backup strategy** for Postgres or Fabric ledger data.
- **All-in-one docker-compose** — no production deployment manifests
  (Kubernetes, ECS, etc.).
- **No CI/CD pipeline** — tests run locally only.
- **No load testing** — performance targets defined in requirements but
  not validated under load.
- **Stale config artifact**: `infra/fabric/config/configtx.yaml` is the
  default Fabric sample (`OrdererType: solo`) — not used. The active
  config is `infra/fabric/configtx.yaml`. Should be deleted to avoid
  future confusion.
- **No log rotation or retention policy** — container logs grow unbounded.
- **Redis has no authentication** configured — relies on network isolation
  (Docker bridge). Production: requirepass + TLS.
