# PostgreSQL WAL retention

The platform defaults `postgres.maxSlotWalKeepSize` to `4GB`. It configures
CloudNativePG's `postgresql.parameters.max_slot_wal_keep_size` on every instance,
including replicas, to bound WAL retained by inactive replication slots.
PostgreSQL's upstream default is unlimited, allowing a stalled slot to fill a PVC.

Set the value for the installation's workload and `postgres.storage.size`.
The limit is enforced at checkpoints, not as a hard disk quota: leave room for
database growth, other WAL retention settings and WAL bursts. Setting the value
to `null` omits the parameter and restores the unlimited upstream default.

A replica whose required WAL exceeds the cap can lose its slot and need recovery
from archived WAL or rebuilding from a healthy primary. Keep tested backups.
For a full replica disk, first verify the primary and a backup, inspect WAL and
slot retention, then follow CNPG's storage expansion or instance recreation
procedure. Do not remove WAL files by hand or delete a PVC merely because it is
marked dangling. Preserve the primary and never promote an unsynchronized replica.

- [CNPG replication slots](https://cloudnative-pg.io/docs/1.29/replication/)
- [CNPG disk-full troubleshooting](https://cloudnative-pg.io/docs/1.29/troubleshooting/)
- [CNPG instance recreation](https://cloudnative-pg.io/docs/1.29/kubectl-plugin/#destroy)
