# Periodic checkpoint replication experiment

This bounded experiment creates a fresh disposable nested workload, exports three
full checkpoints with --leave-running, and copies each archive to another host.
Source progress and archive SHA256 are verified after every checkpoint. The
controller then kills only its labelled test container, confirms it stopped, and
restores the latest complete checkpoint on the peer. Earlier checkpoints remain
available but are not individually restored by this test.

Run replication.py with --source user@host --target user@peer --evidence PRIVATE_DIR.
Both hosts must have the existing kit and identical compatible runtimes/images.
This is a serial experiment, not a background replication service or automatic HA.
No fencing under network partitions is implemented. Never use it for production.
Checkpoint duration includes brief freezing; leave-running does not mean zero pause.
The five-second delay is between completed replications, not a five-second RPO.

The workload has no external network, database or mounted data volumes. Its root
filesystem and process checkpoint travel in the archive. External effects,
volume consistency and network continuity remain unqualified. Reverting to an
older checkpoint can repeat effects that already happened outside the container.

## Network requirement for PodMesh

An eventual network layer must preserve each workload's logical identity/address
and update reachability to its active host. Identical private subnet declarations
alone do not establish connectivity or enforce unique ownership. Stable logical
networks, collision detection and explicit ownership transfer must be verified
before enabling networked failover. This experiment does not implement that layer.

## Observed result — 2026-09-11

Three full checkpoints transferred successfully while the source continued.
Latest checkpoint restored after controller-confirmed source SIGKILL. Preserved
workload UUID b67fdf9f-81b1-4cec-a786-a98151bfe659 and inner PID30; inner exec passed.
Counter38 observed before failure, counter26 at first restored output, then27:
rollback is real, and forward progress resumed. Restore/reconciliation/proof took
6.575seconds, not a measured network outage. Only the last checkpoint was restored.
This simulates loss of the application process, not loss of a host or a partition.
Source stopped, destination running. Full evidence remains private on the operator.
