import logging
import os
import signal
import sys
from dataclasses import dataclass

import sqlalchemy as sa
from dotenv import load_dotenv
from sqlalchemy import Engine, Connection, URL

load_dotenv()
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ["DB_PORT"])
DB_DATABASE = os.environ["DB_DATABASE"]
BATCH_SIZE = int(os.environ["BATCH_SIZE"])
RETRY_COUNT = int(os.environ["RETRY_COUNT"])
CHECKPOINT_TABLE = "transaction_history_mobile_cleanup_checkpoint"

logger = logging.getLogger(__name__)

stop_requested = False


def _handle_signal(signum: int, _frame) -> None:
    global stop_requested

    logger.info(
        "Received signal %s. Stopping after current batch...",
        signum,
    )

    stop_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


@dataclass
class BatchResult:
    last_id: int
    finished: bool


class Migration:
    def __init__(self) -> None:
        url = URL.create(
            "postgresql+psycopg",
            username=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_DATABASE,
        )
        self._engine: Engine = sa.create_engine(
            url,
            connect_args={
                "connect_timeout": 10,
            },
        )
        self._connection: Connection

    def dispose_engine(self) -> None:
        self._engine.dispose()

    def execute(self) -> None:
        self._create_checkpoint_table()

        retry_count = 0

        while not stop_requested:
            try:
                result = self._process_batch()

                if result.finished:
                    logger.info(
                        "No more candidates. Cleanup finished. last_id=%s",
                        result.last_id,
                    )
                    return

                retry_count = 0
            except Exception:
                retry_count += 1

                logger.exception(
                    "Batch failed. Retry %s/%s",
                    retry_count,
                    RETRY_COUNT,
                )

                if retry_count >= RETRY_COUNT:
                    logger.error("Retry count exhausted. Stopping container.")
                    raise

        logger.info("Stop requested. Exiting.")

    def _create_checkpoint_table(self) -> None:
        create_table_query = sa.text(f"""
                create table if not exists {CHECKPOINT_TABLE} (
                    id boolean primary key default true,
                    last_id bigint not null default 0,
                    updated_at timestamp not null default now()
                )
            """)

        insert_init_checkpoint_query = sa.text(f"""
                insert into totum.{CHECKPOINT_TABLE} (id, last_id)
                values (true, 0)
                on conflict (id) do nothing
            """)

        with self._engine.begin() as connection:
            connection.execute(create_table_query)
            connection.execute(insert_init_checkpoint_query)

    def _process_batch(self) -> BatchResult:
        metadata = sa.MetaData()

        backup_table = sa.Table(
            "transaction_history_mobile_backup",
            metadata,
            autoload_with=self._engine,
        )

        candidate_query = sa.text("""
            select thm.id
            from totum.transaction_history_mobile as thm
            where thm.id > :last_id
              and not exists (select 1
                              from totum.deposit as d
                              where d.payment_system_id ->> 'v' = thm.payment_system ->> 'v'
                                and d.transaction_id ->> 'v' = thm.txn_id ->> 'v'
                                and d.end_account_id ->> 'v' = thm.end_account_id ->> 'v'
                                and (d.created_at ->> 'v')::timestamp <= thm.__created_at__)
            order by thm.id
            limit :batch_size
            """)

        delete_query = sa.text("""
            delete
            from totum.transaction_history_mobile
            where id = ANY (:ids)
            returning *
            """)

        with self._engine.begin() as connection:
            self._connection = connection

            last_id = self._get_last_id()

            ids = (
                connection.execute(
                    candidate_query,
                    {
                        "last_id": last_id,
                        "batch_size": BATCH_SIZE,
                    },
                )
                .scalars()
                .all()
            )

            if not ids:
                return BatchResult(last_id=last_id, finished=True)

            last_id = ids[-1]

            logger.info(
                "Processing batch: %s...%s, size=%s",
                ids[0],
                last_id,
                len(ids),
            )

            deleted = (
                connection.execute(
                    delete_query,
                    {"ids": ids},
                )
                .mappings()
                .all()
            )

            if deleted:
                connection.execute(
                    backup_table.insert(),
                    [dict(row) for row in deleted],
                )

            self._update_last_id(last_id)

            logger.info(
                "Batch committed last_id=%s, deleted=%s",
                last_id,
                len(deleted),
            )

            return BatchResult(last_id=last_id, finished=False)

    def _get_last_id(self) -> int:
        query = sa.text(f"""
                    select last_id
                    from totum.{CHECKPOINT_TABLE}
                    where id = true
                    """)
        return int(self._connection.execute(query).scalar_one())

    def _update_last_id(
        self,
        last_id: int,
    ) -> None:
        query = sa.text(f"""
                update totum.{CHECKPOINT_TABLE}
                set last_id = :last_id,
                    updated_at = now()
                where id = true
                """)
        self._connection.execute(
            query,
            {"last_id": last_id},
        )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info(
        "Starting THM cleanup: batch_size=%s retry_count=%s",
        BATCH_SIZE,
        RETRY_COUNT,
    )

    migration = Migration()

    try:
        migration.execute()
        return 0
    except Exception:
        logger.exception("Cleanup failed")
        return 1
    finally:
        migration.dispose_engine()


if __name__ == "__main__":
    sys.exit(main())
