"""Persist bounded, tenant-scoped fixtures for the manual restore test."""
from contextlib import closing
from hashlib import sha256
from http.client import HTTPConnection
import json
import re
import time
from urllib.parse import unquote, urlsplit

TITLE = "Collaudo backup/restore"
TABLE = "truchat_backup_probe"
KEY_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}")
MILVUS_FIELDS = ("MILVUS_HOST", "MILVUS_PORT", "MILVUS_TOKEN", "MILVUS_DB_NAME")


def command(message):
    if isinstance(message, str):
        words = message.split(maxsplit=2)
        if words and words[0] in ("/salva", "/leggi"):
            return words
    return None


def chat_history(entries):
    after_probe = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if entry.get("role") == "user":
            after_probe = command(content) is not None
            if after_probe:
                continue
        elif entry.get("role") == "assistant":
            if after_probe or (isinstance(content, str) and content.startswith(TITLE + "\n")):
                after_probe = False
                continue
        yield entry


def postgres_connection(args):
    from pg8000 import dbapi

    url = urlsplit(args["POSTGRES_URL"])
    return dbapi.connect(
        host=url.hostname, port=url.port or 5432, user=unquote(url.username),
        password=unquote(url.password), database=unquote(url.path[1:]), timeout=5,
    )


def postgres_value(args, key, value=None):
    with closing(postgres_connection(args)) as connection, closing(connection.cursor()) as cursor:
        if value is not None:
            # OPS sets the search path to the tenant's own schema.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (TABLE,))
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (name text PRIMARY KEY, value text NOT NULL)")
            cursor.execute(f"INSERT INTO {TABLE} (name, value) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING", (key, value))
        else:
            cursor.execute("SELECT to_regclass(%s)", (TABLE,))
            if cursor.fetchone()[0] is None:
                return None
        cursor.execute(f"SELECT value FROM {TABLE} WHERE name = %s", (key,))
        row = cursor.fetchone()
        if value is not None:
            connection.commit()
        return row[0] if row else None


def redis_value(args, key, value=None):
    from redis import Redis

    key = args["REDIS_PREFIX"] + "truchat:backup:" + key
    with Redis.from_url(args["REDIS_URL"], decode_responses=True, socket_connect_timeout=5, socket_timeout=5) as client:
        if value is not None:
            client.set(key, value, nx=True)
        return client.get(key)


def mongo_value(args, key, value=None):
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError

    with MongoClient(args["MONGODB_URL"], retryWrites=False, serverSelectionTimeoutMS=3000,
                     connectTimeoutMS=3000, socketTimeoutMS=5000) as client:
        collection = client.get_default_database()[TABLE]
        row = collection.find_one({"_id": key})
        if row is None and value is not None:
            try:
                collection.insert_one({"_id": key, "value": value})
            except DuplicateKeyError:
                # Read the original value if another writer won the insert.
                pass
            row = collection.find_one({"_id": key})
        return row["value"] if row is not None else None


def milvus_request(args, operation, **fields):
    body = json.dumps({"dbName": args["MILVUS_DB_NAME"], "collectionName": TABLE, **fields})
    with closing(HTTPConnection(args["MILVUS_HOST"], int(args["MILVUS_PORT"]), timeout=6)) as connection:
        connection.request("POST", "/v2/vectordb/" + operation, body, {
            "Authorization": "Bearer " + args["MILVUS_TOKEN"],
            "Content-Type": "application/json",
            "Request-Timeout": "5",
        })
        response = connection.getresponse()
        raw = response.read(16385)
        if response.status != 200 or len(raw) > 16384:
            raise ValueError("Milvus response unavailable")
    result = json.loads(raw)
    if result.get("code") != 0:
        raise ValueError("Milvus operation failed")
    return result["data"]


def probe_vector(value):
    # A deterministic FloatVector fixture that can be checked after restore.
    return [(byte + 1) / 256 for byte in sha256(value.encode("utf-8")).digest()[:4]]


def milvus_read(args, key):
    rows = milvus_request(args, "entities/query", filter="id == " + json.dumps(key),
                          outputFields=["id", "value", "vector"], limit=2, consistencyLevel="Strong")
    if not rows:
        return None
    if len(rows) != 1 or rows[0]["id"] != key or rows[0]["vector"] != probe_vector(rows[0]["value"]):
        raise ValueError("Milvus fixture verification failed")
    return rows[0]["value"]


def milvus_value(args, key, value=None):
    if value is None:
        exists = milvus_request(args, "collections/has")["has"]
        return milvus_read(args, key) if exists else None

    # Milvus insert does not enforce unique primary keys. Serialize TruChat's
    # collection creation and read-before-insert with a tenant-local SQL lock.
    with closing(postgres_connection(args)) as connection, closing(connection.cursor()) as cursor:
        cursor.execute("SET LOCAL lock_timeout = '4s'")
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (TABLE + "_milvus",))
        if not milvus_request(args, "collections/has")["has"]:
            fields = [
                {"fieldName": "id", "dataType": "VarChar", "isPrimary": True, "elementTypeParams": {"max_length": "48"}},
                {"fieldName": "value", "dataType": "VarChar", "isPrimary": False, "elementTypeParams": {"max_length": "256"}},
                {"fieldName": "vector", "dataType": "FloatVector", "isPrimary": False, "elementTypeParams": {"dim": "4"}},
            ]
            milvus_request(args, "collections/create", schema={"autoID": False, "enableDynamicField": False, "fields": fields},
                           indexParams=[{"fieldName": "vector", "indexName": "vector", "metricType": "COSINE",
                                         "params": {"index_type": "FLAT"}}])
        for _ in range(12):
            if milvus_request(args, "collections/describe")["load"] == "LoadStateLoaded":
                break
            time.sleep(0.25)
        else:
            raise ValueError("Milvus collection is not ready")
        stored = milvus_read(args, key)
        if stored is not None:
            return stored
        result = milvus_request(args, "entities/insert", partitionName="_default",
                                data=[{"id": key, "value": value, "vector": probe_vector(value)}])
        if result["insertCount"] != 1:
            raise ValueError("Milvus fixture insert failed")
        return milvus_read(args, key)


def observe(read, args, key, value):
    try:
        return read(args, key, value), None
    except Exception:
        # Driver exceptions can contain service addresses or supplied values.
        return None, "non disponibile"


def display(value, issue):
    return issue or ("assente" if value is None else json.dumps(value, ensure_ascii=False))


def reply(args, words):
    yield TITLE + "\n\n"
    # OpenWhisk inserts reserved __ow_* arguments for every web invocation.
    # The Management tunnel invokes the private action with tenant credentials.
    if any(name.startswith("__ow_") for name in args):
        yield "Apri Truchat tramite la Management per usare questi comandi."
        return

    writing = words[0] == "/salva"
    if len(words) != (3 if writing else 2) or not KEY_PATTERN.fullmatch(words[1]):
        yield "Usa `/salva <chiave> <valore>` oppure `/leggi <chiave>`. La chiave può contenere lettere, cifre, trattini e underscore."
        return
    key = words[1]
    value = words[2] if writing else None
    if writing and (len(value.encode("utf-8")) > 256 or "\0" in value):
        yield "Inserisci un valore testuale di massimo 256 byte."
        return

    services = [("PostgreSQL", postgres_value), ("Redis", redis_value)]
    if args.get("MONGODB_URL"):
        services.append(("MongoDB / FerretDB", mongo_value))
    if any(args.get(name) for name in MILVUS_FIELDS):
        services.append(("Milvus", milvus_value))

    readings = {name: observe(probe, args, key, None) for name, probe in services}
    conflict = writing and any(stored is not None and stored != value for stored, _ in readings.values())
    if writing and not conflict and not any(issue for _, issue in readings.values()):
        for name, probe in services:
            if readings[name][0] is None:
                readings[name] = observe(probe, args, key, value)
                if readings[name] != (value, None):
                    break

    yield f"Chiave `{key}`\n\n"
    for name, (stored, issue) in readings.items():
        yield f"    {name}: {display(stored, issue)}\n"
    yield "\n"
    if writing:
        if all(result == (value, None) for result in readings.values()):
            yield "Salvataggio confermato su tutti i servizi configurati."
        elif any(stored is not None and stored != value for stored, _ in readings.values()):
            yield "La chiave contiene un altro valore. Usa una nuova chiave per una nuova prova."
        else:
            yield "Salvataggio incompleto. Puoi ripetere lo stesso comando con lo stesso valore."
    elif all(stored is not None and issue is None for stored, issue in readings.values()) and len(
        {stored for stored, _ in readings.values()}
    ) == 1:
        yield "Tutti i servizi configurati restituiscono lo stesso valore."
    else:
        yield "Verifica incompleta. Per il collaudo servono gli stessi valori su tutti i servizi configurati."
