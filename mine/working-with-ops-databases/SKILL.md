---
name: working-with-ops-databases
description: Use when working with the local PostgreSQL or MariaDB database of an Ops-managed Fuse site, including schema inspection, read queries, data changes, imports, exports, or Ops database troubleshooting.
---

# Working With Ops Databases

Apply this skill only when the current project is managed by local Ops and the requested work concerns its configured PostgreSQL or MariaDB database. Confirm the project from its `.env`, `.env.example`, or equivalent checked-in configuration by finding an Ops-specific setting such as `OPS_PROJECT_DB_NAME`, `OPS_PROJECT_DB_TYPE`, or `OPS_PROJECT_BACKEND`. Do not use this skill for remote databases or unrelated local databases.

## Resolve The Target

1. Read only the non-secret settings needed to identify the database: `OPS_PROJECT_DB_NAME`, `OPS_PROJECT_DB_TYPE`, `DATABASES_DEFAULT_TYPE`, `DATABASES_DEFAULT_NAME`, `DATABASES_DEFAULT_HOST`, `DATABASES_DEFAULT_PORT`, and `DATABASES_DEFAULT_USER`.
2. Prefer the `DATABASES_DEFAULT_*` connection settings when they clearly identify the active application database. Otherwise use the `OPS_PROJECT_DB_*` settings.
3. Map `pdo_pgsql`, `pgsql`, `postgres`, or a `postgres*` host to PostgreSQL. Map `pdo_mysql`, `mysql`, `mariadb`, or a `mariadb` host to MariaDB.
4. If the type or name is absent, conflicting, or could identify more than one database, ask the user before running any database command.
5. Never read, print, or transmit password values, connection URLs containing credentials, or other secrets from `.env` files.

## Prefer Ops Commands

Use the non-interactive Ops client wrappers first. They work without a TTY and avoid coupling to a Docker implementation or container name.

| Database | Discovery | Query pattern |
| --- | --- | --- |
| PostgreSQL | `ops pg list` | `ops pg run -d "<database>" -t -A -c "<sql>"` |
| MariaDB | `ops my list` | `ops my run -D "<database>" -N -B -e "<sql>"` |

Do not run `ops pg cli`, `ops my cli`, or `ops dbsh`: they require an interactive TTY. Before substantive work, run a minimal read-only identity query against the resolved database:

```sh
ops pg run -d "<database>" -t -A -c 'SELECT current_database(), current_user;'
ops my run -D "<database>" -N -B -e 'SELECT DATABASE(), CURRENT_USER();'
```

Use only the command matching the resolved database type. Limit result sets by default and request only the columns needed to answer the question.

## Read-Only And State Changes

When the database is unambiguously relevant to the current project, agents may run read-only SQL without asking first. This includes `SELECT`, `SHOW`, catalog or `information_schema` inspection, and `EXPLAIN`.

Require explicit user confirmation immediately before every state-changing action. State the exact command or SQL and its target database. Confirmation is required for:

- `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`, or stored procedure calls that may write data.
- `CREATE`, `ALTER`, `DROP`, `GRANT`, `REVOKE`, migrations, and other DDL or permission changes.
- `ops pg create`, `ops pg drop`, `ops my create`, `ops my drop`, `ops pg import`, `ops my import`, `ops sync`, and any command that can replace database contents.
- Exports that overwrite an existing file, shell pipelines with uncertain database effects, and commands whose SQL effect cannot be established.

After an approved mutation, verify the requested result with a separate read-only query. Do not blindly retry a failed mutation.

## Docker Fallback

Use direct Docker only when the equivalent Ops command cannot perform the request or fails. First inspect service state with `ops ps`; if needed, use `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'` to identify the running PostgreSQL or MariaDB service. Do not assume fixed names such as `ops-postgres16-1` or `ops-mariadb-1`.

Use the discovered container with the same read-only-first and confirmation rules:

```sh
docker exec "<postgres-container>" psql -U "<user>" -d "<database>" -t -A -c "<sql>"
docker exec "<mariadb-container>" mariadb -u "<user>" -D "<database>" -N -B -e "<sql>"
```

If discovery does not identify one applicable running service, report the error and ask the user how to proceed rather than guessing.
