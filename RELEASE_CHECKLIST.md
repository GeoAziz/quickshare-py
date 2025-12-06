Release checklist for QuickSharePy (next-gen P2P transfer)

Release preparation steps

- [x] Code: all core modules implemented
  - `quickshare/discovery.py` — UDP discovery announcer/listener
  - `quickshare/control.py` — TCP control handshake (offer/accept)
  - `quickshare/transfer.py` — Sender/Receiver with parallel chunk streams
  - `quickshare/fileutils.py` — preallocation, chunk writes, hashing
  - `quickshare/cli.py` — CLI: list, send, recv with progress/ETA

- [x] Tests
  - `tests/test_transfer.py` — loopback 1-chunk and 8-chunk tests
  - `tests/test_discovery.py` — loopback discovery test
  - All tests currently pass: `PYTHONPATH=$(pwd) pytest -q` -> 5 passed

- [x] CLI behavior
  - `list` discovers peers (announces local node while collecting peers)
  - `send` sends selected file with single-line progress, throughput and ETA
  - `recv` listens for offers and saves received files

- [x] Robustness features
  - Per-block streaming (64 KiB blocks) for smooth progress
  - Rate-limited UI (max 4 Hz) to avoid terminal flicker
  - Connection retries (3 attempts, exponential backoff)
  - Block send retries (2 attempts) with short backoff

- [x] Security & integrity
  - Per-chunk SHA256 verification via `fileutils.get_chunk_sha256`
  - Full-file SHA256 computed at receiver finalize via `fileutils.sha256_file`

- [x] Documentation
  - `README.md` updated with usage examples, CLI commands, and notes
  - Release checklist (this file) added

Optional follow-ups (post-release)

- Resumable chunk protocol to survive mid-chunk disconnections
- TLS/DTLS encryption for control and/or data channels
- EMA smoothing for throughput and ETA
- Per-chunk / per-stream statistics and logs
- Better CLI UX: interactive discovery loop, progress bars with estimated time remaining per chunk

Release tasks

1. Run full test suite:
   PYTHONPATH=$(pwd) pytest -q
2. Tag the release (example):
   git tag -a v1.0.0 -m "QuickSharePy next-gen P2P transfer release"
   git push --tags
3. Update CHANGELOG.md with summaries of major changes
4. Publish release notes (GitHub Releases) with usage examples from README

Notes

- This release focuses on LAN performance and UX (parallel streams, progress/ETA, retries).
- The current design trades off mid-chunk resumability for a simpler protocol and higher throughput; resumability is planned for a future minor release.
