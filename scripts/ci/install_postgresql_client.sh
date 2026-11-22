#!/usr/bin/env bash
# Install PostgreSQL from the PGDG apt repo at the given major version.
# Ubuntu's own postgresql packages are older than the server we test against,
# and pg_dump refuses to dump a server newer than itself.
#
# usage: install_postgresql_client.sh <major> [client|server]
#   client (default): postgresql-client-<major> (psql, pg_dump, ...)
#   server: postgresql-<major> (adds initdb, pg_ctl for throwaway clusters)
set -euo pipefail

major="${1:?usage: install_postgresql_client.sh <major-version> [client|server]}"
kind="${2:-client}"

case "${kind}" in
  client) package="postgresql-client-${major}" ;;
  server) package="postgresql-${major}" ;;
  *) echo "unknown kind: ${kind} (expected client or server)" >&2; exit 2 ;;
esac

sudo apt-get update
sudo apt-get install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get install -y "${package}"

# The versioned binaries live under /usr/lib/postgresql/<major>/bin; put them first.
echo "/usr/lib/postgresql/${major}/bin" >> "${GITHUB_PATH:-/dev/null}"
export PATH="/usr/lib/postgresql/${major}/bin:${PATH}"

psql --version
pg_dump --version
if [[ "${kind}" == "server" ]]; then
  initdb --version
fi
