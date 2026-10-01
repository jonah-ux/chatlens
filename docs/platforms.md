# Platform support

Chatlens is verified on Linux and macOS through the hosted Python 3.11/3.12 fixture, package, and installed-consumer matrix.

Windows is currently outside the supported verification boundary. A trial Windows CI leg on 2026-10-01 ran the synthetic suite but failed in several contracts that depend on POSIX behavior: private cache mode `0700`/`0600` assertions, permission-based cache refusal, fixture recovery paths, and some source-inventory status expectations. These failures mean the project does not yet have evidence for claiming Windows support.

The boundary is deliberate:

- no Windows support claim is made from the fact that Python itself runs on Windows;
- native transcript discovery is not tested against real user stores on any platform;
- Windows support requires a separate path/permission contract, Windows-specific fixtures, and a full installed-consumer matrix before the README can change;
- users needing a verified path should use Linux or macOS until that work is complete.

The synthetic fixtures remain local and credential-free. This document records a limitation discovered by CI; it does not claim that every Windows filesystem or Python runtime behaves identically.
