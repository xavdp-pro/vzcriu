# Execution results

Date: 2026-09-11. Experimental dedicated Debian 13 guests; network-free nested counter workload.

These are fresh checkpoints and automatic restoration/reconciliation, not manually normalized archives.

| Run | Counter before / after | Total controller seconds | Inner exec |
| --- | --- | --- | --- |
| migration-334bbd6dbd394541b420718bd30a1cf2 | 78 / 80 | 8.583 | INNER_EXEC_OK |
| migration-411deab09c804ecb836589fe4b260e6e | 132 / 134 | 9.932 | INNER_EXEC_OK |
| migration-b147a712c99a472684bcb2f24b363a9f | 65 / 67 | 9.829 | INNER_EXEC_OK |

All three retained the same workload UUID and inner PID 30. Directions were A to B, B to A, A to B. Original source stopped in each case. Fresh counter increments were required after restore. The total includes SSH calls, transfer and verification; it is not CRIU-only time or a downtime benchmark. No subsecond claim is made.

An occupied destination was rejected before checkpoint; a subsequent source proof confirmed it still running with the original UUID and advancing counter.

The tested CRIU binary SHA-256 was `4ecb663e7e3b019cdfa534c0d4cce4134938f87ae3b45cac35a7481c0a947cd4`. Versions: Podman 5.4.2, crun 1.21, kernel 6.12.107+deb13-amd64 x86_64. The preinstalled outer image was migration-lab:extended (Debian 13 plus Python/Podman). The included Containerfile describes a minimal equivalent but has not itself been rebuilt in this run.

## Feature reconciliation

K1: binary compiled and matching installations verified. Package installation recipes documented; not a clean-room build test. K2-K5: exercised through the public kit CLI. K6: repeated forward and reverse operations, as above. K7: occupied-destination rejection tested; other failure modes remain unqualified. K8: English public kit prepared; no upstream acceptance claimed.

## Three-pass review

1. Governance: restricted to labelled disposable lab workloads; evidence distinguishes observed behavior from general capability. No production promises.
2. Operator experience: documented commands, retained checkpoints, explicit failed-state recovery and limitations.
3. Runtime: independent code counter-view identified stale-log proof, late preflight and first-log race; addressed before migration tests. Single-controller operation only; distributed fencing, automatic rollback and arbitrary workload compatibility remain open.

Overall: experimental advance with known limitations. No proof for networked universes, arbitrary rseq users, overlay storage, exact socket getsockname preservation, or production workloads. Raw memory archives are retained privately, not published.

## Debian runtime packaging (2026-09-11)

`podmesh-vzcriu 3.15.5.3+podmesh1~experimental1` was built and published through the signed experimental APT repository. Installation on all three Debian 13 amd64 lab hosts succeeded. `check-runtime-package.sh` verified the expected binary hash, runnable version command, resolved shared libraries and absence of ownership of the system CRIU executable paths. This is package-delivery evidence, not an additional migration run.

Three-pass review: governance preserves an explicitly named, optional runtime; operator documentation distinguishes package installation from complete migration setup; runtime checks confirm identical tested binary bytes and dependencies. No independent counter-review was available for this packaging-only change. Remaining work includes clean-host dependency testing and the complete helper/API migration chain.
