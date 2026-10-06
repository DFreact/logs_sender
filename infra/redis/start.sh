#!/bin/sh
set -eu
umask 077
read -r hub_redis_secret < /run/secrets/redis_password
case "$hub_redis_secret" in
  *[!a-zA-Z0-9_-]*|'') exit 1 ;;
esac
{
  printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\n'
  printf 'requirepass %s\n' "$hub_redis_secret"
  printf 'save ""\nappendonly no\ndir /tmp\nmaxmemory 128mb\nmaxmemory-policy noeviction\n'
} > /tmp/eventhub-redis.conf
unset hub_redis_secret
exec redis-server /tmp/eventhub-redis.conf
