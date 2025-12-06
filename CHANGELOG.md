# Changelog

All notable changes to this project will be documented in this file.

The format is based on "Keep a Changelog" and this file records the v1.0.0
release of the next-generation parallel P2P transfer engine added to
QuickSharePy.

## [1.0.0] - 2025-12-06
### Added
- New parallel P2P transfer engine alongside the original HTTP share script.
  - `quickshare/discovery.py` — UDP announcer & listener (DiscoveryManager).
  - `quickshare/control.py` — TCP control handshake (ControlServer, send_control_offer).
  - `quickshare/transfer.py` — Sender/Receiver: parallel chunk-based transfer with per-chunk workers.
  - `quickshare/fileutils.py` — preallocation, chunk writes, per-chunk and full-file SHA256 helpers.
  - `quickshare/cli.py` — CLI with `list`, `send`, and `recv` commands, single-line progress bar, throughput and ETA.

- Robustness & UX features:
  - Peer discovery via UDP announcements (default port 37020).
  - Per-chunk SHA256 verification and full-file SHA256 validation at receiver finalization.
  - Chunk streaming in 64 KiB blocks for smooth progress reporting.
  - Single-line progress bar with rate-limited updates (max 4 Hz) showing percent, chunks, bytes, KB/s, and ETA.
  - Connection retries (3 attempts) with exponential backoff and per-block send retries (2 attempts).

### Tests
- `tests/test_transfer.py` — loopback tests for 1-chunk and 8-chunk transfers.
- `tests/test_discovery.py` — loopback discovery test.

### Documentation
- README updated with usage examples, progress/ETA notes, configuration options, and robustness notes.
- `RELEASE_CHECKLIST.md` added documenting release steps and follow-ups.

### Notes & Future Work
- Current protocol does not support mid-chunk resume; resumable chunks are a planned enhancement.
- Future improvements: TLS/DTLS for control/data, EMA smoothing for ETA, transfer logging, and per-stream stats.

---

Generated on 2025-12-06.
