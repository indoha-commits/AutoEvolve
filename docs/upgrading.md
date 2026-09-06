# Upgrading and backups

Company Core stores operational state under `data/` and generated project artifacts under
`projects/`. Both are intentionally outside Git.

## Back up

Stop the application before copying SQLite files:

```bash
mkdir -p ../company-core-backup
cp -a .env config data projects engines/g3/config/g3.env ../company-core-backup/
```

Protect the backup because it can contain credentials, contacts, emails, and campaign media. For
automated backups, use encrypted storage and retain multiple tested restore points.

## Upgrade

```bash
git fetch --tags
git pull --ff-only
make setup
make check
```

Read `CHANGELOG.md` before restarting. `make setup` preserves the existing `.env` and local config.
Compare `.env.example` with `.env` manually to discover newly added settings.

## Restore or roll back

Stop the application, check out the previous known-good tag or commit, reinstall its dependencies,
then restore the matching `data/` and `projects/` backup. Do not run an older release against data
that has undergone an incompatible migration unless the release notes explicitly allow it.

After any restore, run `make doctor`, start the service, verify `/health`, and inspect one saved lead
and campaign before resuming outbound actions.
