import time
import json
import os
import logging
import pandas as pd
from azure.eventhub import EventHubProducerClient, EventData

# --- KONFIGURATION ---
CONN_STR = os.getenv("EVENT_HUB_CONN_STR")
EVENT_HUB_NAME = "commits"
CSV_FILE = "infected_code.csv"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSV-Producer")

def run():
    if not CONN_STR:
        logger.error("Kein Connection String gefunden!")
        return

    if not os.path.exists(CSV_FILE):
        logger.error(f"Datei {CSV_FILE} nicht gefunden! Bitte ins Image kopieren.")
        return

    client = EventHubProducerClient.from_connection_string(CONN_STR, eventhub_name=EVENT_HUB_NAME)
    
    logger.info(f"Starte Streaming von {CSV_FILE}...")

    chunk_iterator = pd.read_csv(CSV_FILE, chunksize=50, nrows=200)

    with client:
        for chunk in chunk_iterator:
            batch = client.create_batch()
            
            for index, row in chunk.iterrows():
                event_dict = row.to_dict()
                
                # Echtzeit-Simulation!
                # alten Timestamp mit 'jetzt' überschreiben, 
                event_dict['ingest_timestamp'] = time.time()
                
                try:
                    batch.add(EventData(json.dumps(event_dict)))
                except ValueError:
                    client.send_batch(batch)
                    logger.info("Batch gesendet (Limit erreicht)")
                    batch = client.create_batch()
                    batch.add(EventData(json.dumps(event_dict)))
            
            if len(batch) > 0:
                client.send_batch(batch)
                logger.info(f"Chunk gesendet ({len(chunk)} Events).")

            time.sleep(1.0)

    logger.info("CSV vollständig verarbeitet.")

if __name__ == "__main__":
    run()