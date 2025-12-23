import asyncio
import os
import json
import uuid
import logging
from datetime import datetime
from azure.eventhub.aio import EventHubConsumerClient
from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore
from azure.storage.filedatalake import DataLakeServiceClient
from sqlalchemy import create_engine, text

# --- KONFIGURATION ---
EH_CONN_STR = os.getenv("EVENT_HUB_CONN_STR")
EH_NAME = "commits"
STORAGE_CONN_STR = os.getenv("STORAGE_CONN_STR")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = "postgres"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Processor")

DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:5432/{DB_NAME}?sslmode=require"

# --- SETUP ---
try:
    dl_service_client = DataLakeServiceClient.from_connection_string(STORAGE_CONN_STR)
    file_system_client = dl_service_client.get_file_system_client("rawdata")
    logger.info("Data Lake Client OK.")
except Exception as e:
    logger.error(f"DL Init Error: {e}")
    file_system_client = None

try:
    engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
    logger.info("DB Engine OK.")
except Exception as e:
    logger.error(f"DB Init Error: {e}")
    engine = None

async def on_event_batch(partition_context, events):
    if not events:
        return

    logger.info(f"Verarbeite Batch mit {len(events)} Events...")
    batch_data_list = []

    for event in events:
        try:
            body = event.body_as_str()
            data = json.loads(body)
            batch_data_list.append(data)
        except Exception:
            continue

    if not batch_data_list:
        await partition_context.update_checkpoint()
        return

    # --- COLD PATH ---
    if file_system_client:
        try:
            json_payload = json.dumps(batch_data_list)
            file_name = f"batch_{uuid.uuid4()}.json"
            file_client = file_system_client.get_file_client(file_name)
            file_client.upload_data(json_payload, overwrite=True)
            logger.info(f"Cold Path: {file_name} gespeichert.")
        except Exception as e:
            logger.error(f"Cold Path Fehler: {e}")

    # --- HOT PATH ---
    if engine:
        try:
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS commit_stats (
                        id SERIAL PRIMARY KEY,
                        commit_id VARCHAR(255),
                        author VARCHAR(255),
                        ingest_time TIMESTAMP
                    )
                """))
                
                for item in batch_data_list:
                    c_id = item.get("commit_id", "unknown")
                    
                    auth = item.get("author", "unknown")
                    
                    ts_raw = item.get("ingest_timestamp")
                    if ts_raw:
                        final_time = datetime.fromtimestamp(float(ts_raw))
                    else:
                        final_time = datetime.now()

                    conn.execute(text("""
                        INSERT INTO commit_stats (commit_id, author, ingest_time) 
                        VALUES (:cid, :auth, :time)
                    """), {
                        "cid": c_id,
                        "auth": auth,
                        "time": final_time
                    })
                conn.commit()
                logger.info(f"Hot Path: {len(batch_data_list)} Zeilen (Simple Schema) gespeichert.")
        except Exception as e:
            logger.error(f"Hot Path Fehler: {e}")

    await partition_context.update_checkpoint()

async def main():
    checkpoint_store = BlobCheckpointStore.from_connection_string(STORAGE_CONN_STR, "checkpoints")
    client = EventHubConsumerClient.from_connection_string(
        EH_CONN_STR,
        consumer_group="$Default",
        eventhub_name=EH_NAME,
        checkpoint_store=checkpoint_store
    )
    async with client:
        await client.receive_batch(
            on_event_batch=on_event_batch, 
            max_batch_size=50, 
            starting_position="-1"
        )

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())