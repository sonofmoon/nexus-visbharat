# External Audit Anchoring Specification: Nexus-Visbharath
**Document ID:** SPEC-ANCHOR-2026-V1  
**Status:** Approved Technical Architecture Specification  
**Classification:** Public Security & Audit Specification  
**Standard References:** RFC 3161 (Internet X.509 PKI Time-Stamp Protocol), RFC 6962 (Certificate Transparency / Rekor Merkle Trees)

---

## 1. Context & Architectural Motivation

Nexus-Visbharath (NVB) maintains an internal, append-only, SHA-256 tamper-evident ledger (`visbharat/audit.py`) that cryptographically links all citizen grievance intake events, triage decisions, and administrative resource allocations:

$$H_i = \text{SHA-256}(H_{i-1} \,\|\, t_i \,\|\, \text{actor}_i \,\|\, \text{action}_i \,\|\, \text{SHA-256}(\text{payload}_i))$$

### The Internal-Chain Threat Scenario
While the internal chain guarantees tamper-evidence against unprivileged attackers, an adversary possessing full database administrative privileges (e.g. root Cloud SQL access) could theoretically alter a historical record at index $k$ and recompute all downstream hashes $H_k, H_{k+1}, \dots, H_n$ to present a falsified yet internally consistent ledger.

To eliminate this threat vector and provide statutory non-repudiation under Indian evidentiary standards (Indian Evidence Act / Information Technology Act 2000), NVB specifies **External Anchoring**: periodic publishing of ledger states to independent, verifiable, external trust anchors.

```
+-----------------------------------------------------------------------------------+
|                        INTERNAL AUDIT LEDGER (SHA-256)                            |
|  [Block 0] <--- [Block 1] <--- [Block 2] <--- ... <--- [Block N]                   |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                            MERKLE TREE BATCH AGGREGATOR                           |
|  Leaves: Hash of each ledger event within batch window (1 hour / 1000 events)    |
|  Root Hash: M_root = MerkleRoot(H_0, H_1, ..., H_m)                               |
+-----------------------------------------------------------------------------------+
                         |                               |
                         v                               v
+------------------------------------+   +------------------------------------+
|       RFC 3161 TRUSTED TIMESTAMP   |   |     PUBLIC TRANSPARENCY LOG        |
| - Submits M_root to Accredited     |   | - Sigstore Rekor / Trillian        |
|   Indian TSA (e-Mudhra / CDAC)     |   | - Append-only verifiable log       |
| - Receives cryptographically       |   | - Publicly queryable inclusion     |
|   signed TimeStampToken (TST)      |   |   proofs across all jurisdictions  |
+------------------------------------+   +------------------------------------+
```

---

## 2. Technical Architecture & Protocols

### 2.1 Merkle Tree Batch Aggregation
Rather than anchoring every individual grievance event externally (which incurs prohibitive latency and cost), NVB batches ledger records into epochs:
- **Epoch Trigger:** Every 60 minutes or every 1,000 grievance/allocation events (whichever threshold is reached first).
- **Leaf Construction:** For ledger entry $i$, the leaf hash is $L_i = \text{SHA-256}(H_i)$.
- **Root Derivation:** Complete binary Merkle tree constructed over all leaves $L_0, L_1, \dots, L_{m-1}$ yields Merkle Root $M_{\text{epoch}}$.

### 2.2 RFC 3161 Trusted Timestamp Authority (TSA)
For domestic legal validity in India:
1. NVB generates a `TimeStampReq` structure containing $M_{\text{epoch}}$ and nonce.
2. The request is transmitted over HTTPS to an accredited Indian Certifying Authority (CA) operating an RFC 3161 compliant TSA (e.g., e-Mudhra, CDAC, or National Informatics Centre).
3. The TSA responds with a signed `TimeStampResp` containing the cryptographic `TimeStampToken` (TST) with an X.509 certificate chain rooted in the Controller of Certifying Authorities (CCA) India trust store.
4. The TST is stored in the database alongside the Merkle root record.

### 2.3 Public Transparency Log (Sigstore Rekor)
For global, open, decentralized auditability:
1. $M_{\text{epoch}}$ is submitted as a hashedrekord entry to the public Rekor transparency log (`https://rekor.sigstore.dev`).
2. Rekor verifies the payload, appends the entry to its verifiable Merkle tree, and returns:
   - `logIndex`: Zero-based sequential position in the transparency log.
   - `integratedTime`: Epoch timestamp when appended.
   - `signedEntryTimestamp` (SET): Cryptographically signed commitment from Rekor.
3. Anyone can inspect and verify the inclusion of NVB ledger epochs using the standard Rekor CLI:
   ```bash
   rekor-cli get --log-index <index>
   ```

---

## 3. Verification & Proof of Inclusion

Citizens, municipal analysts, and independent civil-society auditors can verify any historical transaction using a two-tier proof:

1. **Internal In-Chain Verification:**
   Verify that entry $e_i$ correctly chains into the internal hash $H_i$, and that $H_i$ is part of epoch batch $B$.
2. **Merkle Inclusion Proof:**
   Verify that $L_i = \text{SHA-256}(H_i)$ is included in $M_{\text{epoch}}$ via the $O(\log_2 m)$ Merkle audit path:
   $$\text{VerifyPath}(L_i, \text{Path}_i, M_{\text{epoch}}) \equiv \text{True}$$
3. **External Trust Root Verification:**
   - **RFC 3161:** Validate that the TSA signature on $M_{\text{epoch}}$ decrypts with the CCA public key and that the timestamp matches the operational window.
   - **Rekor:** Validate the Signed Entry Timestamp (SET) against Sigstore's public key.

---

## 4. API Endpoints & Contract

The following endpoints support external anchoring telemetry:

| Endpoint | Method | Role | Description |
| :--- | :--- | :--- | :--- |
| `/api/audit/ledger` | `GET` | `auditor`, `admin` | Returns paginated internal SHA-256 audit ledger with running hashes. |
| `/api/audit/verify` | `GET` | `auditor`, `admin` | Recomputes full internal hash chain and reports verification status (`is_valid: true`). |
| `/api/audit/anchors` | `GET` | `public` | Lists published epoch Merkle roots, RFC 3161 timestamp receipts, and Rekor log indices. |
| `/api/audit/proof/<entry_id>` | `GET` | `public` | Returns cryptographic Merkle inclusion path for an individual grievance entry. |

---

## 5. Security & Privacy Guarantees

- **Zero PII in External Anchors:** Only cryptographically one-way Merkle roots ($M_{\text{epoch}}$) are transmitted externally. No citizen names, phone numbers, or grievance descriptions ever leave the secure perimeter.
- **Permanent Tamper Resistance:** Once an epoch Merkle root is committed to an RFC 3161 TSA and Rekor log, even a full compromise of the database server cannot alter historical records without failing external cryptographic verification.
