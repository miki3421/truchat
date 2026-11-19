#--kind python:default
#--web true
#--timeout 300000
#--param AI_API_KEY "$AI_API_KEY"
#--param AI_BASE_URL "$AI_BASE_URL"
#--param AI_CHAT_MODEL "$AI_CHAT_MODEL"
#--param REDIS_URL "$REDIS_URL"
#--param REDIS_PREFIX "$REDIS_PREFIX"
#--param POSTGRES_URL "$POSTGRES_URL"
#--param MONGODB_URL "$MONGODB_URL"
#--param MILVUS_HOST "$MILVUS_HOST"
#--param MILVUS_PORT "$MILVUS_PORT"
#--param MILVUS_TOKEN "$MILVUS_TOKEN"
#--param MILVUS_DB_NAME "$MILVUS_DB_NAME"

import chat
def main(args):
  return { "body": chat.chat(args) }
