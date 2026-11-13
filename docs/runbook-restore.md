# Runbook: restore the database from a backup

Backups: a nightly `pg_dump --format=custom` of the whole database, written to
`s3://ukco-ops-<account>/backups/YYYY-MM-DD.dump` at 03:00 UTC by the `ukco-backup` systemd
timer on the server. Kept 30 days (bucket lifecycle rule).

## Restore drills

| Date | Backup | Where | Result |
|---|---|---|---|
| 2026-11-10 | `2026-11-10.dump` (438 KB) | Scratch database on a laptop (`pgvector/pgvector:pg17`) | ✅ `pg_restore --exit-on-error` clean in <1 s; schema at migration 0006; 5 reports, 261 companies, 158 evidence snapshots; every citation in every report re-validated against the restored evidence; the append-only triggers on `reviews` and `chat_messages` came back and still block edits; pgvector 0.8.6 |

Re-run a drill after any migration that changes how evidence or the audit trail is stored.

## Drill (safe: never touches production)

```bash
export AWS_PROFILE=aws-new AWS_REGION=eu-north-1
aws s3 cp s3://ukco-ops-<account>/backups/<date>.dump backup.dump
docker exec <db> psql -U ukco -d postgres -c "CREATE DATABASE restore_test"
docker cp backup.dump <db>:/tmp/backup.dump
docker exec <db> pg_restore -U ukco -d restore_test --no-owner --exit-on-error /tmp/backup.dump
# Check: counts, `alembic_version`, and validate_report() over every stored report
# against DbEvidenceStore on restore_test. Then drop restore_test and delete backup.dump
# (it contains register data about real people).
```

## Real restore on the server (data loss or corruption)

Run through SSM (no SSH): `aws ssm start-session --target <instance-id>`, then as root:

```bash
cd /opt/ukco
dc="docker compose -f deploy/compose.yml --env-file deploy/.env"
aws s3 cp --region eu-north-1 s3://ukco-ops-<account>/backups/<date>.dump /tmp/restore.dump
$dc stop api                                  # no writes during the restore
$dc exec -T db pg_dump -U ukco --format=custom ukco > /tmp/before-restore.dump   # keep the broken state
$dc exec -T db psql -U ukco -d postgres -c "DROP DATABASE ukco WITH (FORCE)" -c "CREATE DATABASE ukco"
$dc exec -T db pg_restore -U ukco -d ukco --no-owner --exit-on-error < /tmp/restore.dump
$dc up -d --wait                              # migrate runs first, then the API
curl -fsS https://<api-host>/health
```

Anything written after the backup's 03:00 UTC timestamp is lost. That includes analysts'
review decisions, which are part of the audit record, so tell reviewers which window to
re-check.
