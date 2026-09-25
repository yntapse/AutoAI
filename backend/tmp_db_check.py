from sqlalchemy import text
from main import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT current_database(), inet_server_addr(), inet_server_port(), current_user")
        ).fetchone()
        print("DB_INFO=", row)
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conname='ck_agent_runs_status'"
            )
        ).fetchall()
        print("CONSTRAINTS=", constraints)
    finally:
        db.close()


if __name__ == "__main__":
    main()
