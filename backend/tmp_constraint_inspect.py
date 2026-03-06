from sqlalchemy import text
from main import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT n.nspname AS schema, c.relname AS table_name, con.conname, "
                "pg_get_constraintdef(con.oid) AS def "
                "FROM pg_constraint con "
                "JOIN pg_class c ON c.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE con.conname='ck_agent_runs_status'"
            )
        ).fetchall()
        print(rows)
    finally:
        db.close()


if __name__ == '__main__':
    main()
