#!/bin/bash
# 每日备份：PostgreSQL / Neo4j / MinIO / saves，外加每周一次 Milvus 向量目录。
#
# 用法：
#   cp scripts/backup.sh /somewhere/backups/ && chmod +x /somewhere/backups/backup.sh
#   crontab: 30 3 * * * PROJECT_ROOT=/path/to/rxyy-know BACKUP_DEST=/somewhere/backups/rxyy-know \
#                       /somewhere/backups/backup.sh >> /somewhere/backups/backup.log 2>&1
#
# 可配置项（均可用环境变量覆盖）：
#   PROJECT_ROOT      项目根目录，默认取脚本所在目录的上一级
#   BACKUP_DEST       备份落盘目录，默认 $PROJECT_ROOT/backups
#   CONTAINER_PREFIX  容器名前缀，需与 compose 中的 container_name 一致
#   POSTGRES_USER / POSTGRES_DB
#   DAILY_KEEP_DAYS / WEEKLY_KEEP
#
# 策略：
#   每日：PG pg_dump（在线一致）+ Neo4j/MinIO/saves tar（建议放在低写入时段）
#   每周日：Milvus 向量目录全量 tar（体积大，且可由 MinIO 原始文件重入库恢复，故降频）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(dirname "$SCRIPT_DIR")}"
VOL="$PROJECT_ROOT/docker/volumes"
DEST="${BACKUP_DEST:-$PROJECT_ROOT/backups}"
CONTAINER_PREFIX="${CONTAINER_PREFIX:-}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-yuxi}"
DATE=$(date +%Y%m%d)
DAILY_KEEP_DAYS="${DAILY_KEEP_DAYS:-14}"
WEEKLY_KEEP="${WEEKLY_KEEP:-4}"

if [ ! -d "$VOL" ]; then
    echo "[fatal] 找不到数据卷目录 $VOL，请用 PROJECT_ROOT 指定项目根目录" >&2
    exit 2
fi

mkdir -p "$DEST/daily" "$DEST/weekly"
echo "===== backup start $(date '+%F %T') ====="
echo "[conf] root=$PROJECT_ROOT dest=$DEST prefix=${CONTAINER_PREFIX:-<none>}"

fail=0

# 1. PostgreSQL（业务元数据，在线 dump 保证一致性）
if docker exec "${CONTAINER_PREFIX}postgres" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DEST/daily/pg-$DATE.sql.gz"; then
    echo "[pg] $(du -h "$DEST/daily/pg-$DATE.sql.gz" | cut -f1)"
else
    echo "[pg] FAILED"; fail=1
fi

# 2. Neo4j 图谱（community 版无在线 dump，低写入时段直接 tar 数据目录）
if tar czf "$DEST/daily/neo4j-$DATE.tar.gz" -C "$VOL" neo4j; then
    echo "[neo4j] $(du -h "$DEST/daily/neo4j-$DATE.tar.gz" | cut -f1)"
else
    echo "[neo4j] FAILED"; fail=1
fi

# 3. MinIO 原始文件（重入库的源头，最重要）
if tar czf "$DEST/daily/minio-$DATE.tar.gz" -C "$VOL/milvus" minio; then
    echo "[minio] $(du -h "$DEST/daily/minio-$DATE.tar.gz" | cut -f1)"
else
    echo "[minio] FAILED"; fail=1
fi

# 4. saves（配置/日志/任务状态）
if tar czf "$DEST/daily/saves-$DATE.tar.gz" -C "$VOL" yuxi; then
    echo "[saves] $(du -h "$DEST/daily/saves-$DATE.tar.gz" | cut -f1)"
else
    echo "[saves] FAILED"; fail=1
fi

# 5. 每周日：Milvus 向量数据
if [ "$(date +%u)" = "7" ]; then
    if tar czf "$DEST/weekly/milvus-$DATE.tar.gz" -C "$VOL" \
        --exclude='milvus/minio' --exclude='milvus/minio_config' --exclude='milvus/logs' milvus; then
        echo "[milvus-weekly] $(du -h "$DEST/weekly/milvus-$DATE.tar.gz" | cut -f1)"
    else
        echo "[milvus-weekly] FAILED"; fail=1
    fi
fi

# 6. 滚动清理
find "$DEST/daily" -name '*.gz' -mtime +"$DAILY_KEEP_DAYS" -delete
ls -t "$DEST/weekly"/milvus-*.tar.gz 2>/dev/null | tail -n +$((WEEKLY_KEEP + 1)) | xargs -r rm -f

echo "[dest] $(du -sh "$DEST" | cut -f1) total, disk free: $(df -h "$DEST" | awk 'NR==2{print $4}')"
echo "===== backup end $(date '+%F %T') exit=$fail ====="
exit $fail
