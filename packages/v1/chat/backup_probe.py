"""Persist bounded, tenant-scoped fixtures for the manual restore test."""
from contextlib import closing
import json
import re
from urllib.parse import unquote, urlsplit

TITLE = "Collaudo backup/restore"
TABLE = "truchat_backup_probe"
KEY_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}")


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


def postgres_value(args, key, value=None):
    from pg8000 import dbapi

    url = urlsplit(args["POSTGRES_URL"])
    with closing(dbapi.connect(
        host=url.hostname, port=url.port or 5432, user=unquote(url.username),
        password=unquote(url.password), database=unquote(url.path[1:]), timeout=5,
    )) as connection, closing(connection.cursor()) as cursor:
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

    postgres, postgres_issue = observe(postgres_value, args, key, value)
    if writing and (postgres_issue or postgres != value):
        redis, redis_issue = None, "scrittura non eseguita"
    else:
        redis, redis_issue = observe(redis_value, args, key, value)
    yield f"Chiave `{key}`\n\n"
    yield f"    Redis: {display(redis, redis_issue)}\n"
    yield f"    PostgreSQL: {display(postgres, postgres_issue)}\n\n"
    if writing:
        if redis == postgres == value:
            yield "Salvataggio confermato su entrambi i servizi."
        elif any(stored is not None and stored != value for stored in (redis, postgres)):
            yield "La chiave contiene un altro valore. Usa una nuova chiave per una nuova prova."
        else:
            yield "Salvataggio incompleto. Puoi ripetere lo stesso comando con lo stesso valore."
    elif not redis_issue and not postgres_issue and redis is not None and redis == postgres:
        yield "I due servizi restituiscono lo stesso valore."
    else:
        yield "Verifica incompleta. Per il collaudo servono entrambi i valori originali."
