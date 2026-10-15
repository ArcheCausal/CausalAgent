#!/usr/bin/env bash
# 一次性迁移：把旧宿主目录里的正式 RAG release 导入 rag_formal 命名卷。
#
# 用法：migrate-rag-storage.sh <旧索引目录> <旧 active pointer 目录> <命名卷名>
# 迁移完成后不删除旧目录；确认无误后由用户自行处理。
set -euo pipefail

old_indexes="${1:?usage: migrate-rag-storage.sh <old-indexes-dir> <old-runtime-dir> <volume-name>}"
old_runtime="${2:?usage: migrate-rag-storage.sh <old-indexes-dir> <old-runtime-dir> <volume-name>}"
volume="${3:?usage: migrate-rag-storage.sh <old-indexes-dir> <old-runtime-dir> <volume-name>}"

docker run --rm \
  -v "${old_indexes}:/src/indexes:ro" \
  -v "${old_runtime}:/src/runtime:ro" \
  -v "${volume}:/dest" \
  alpine:3.20 sh -c '
    set -eu
    mkdir -p /dest/indexes
    cp -a /src/indexes/. /dest/indexes/
    cp -a /src/runtime/active_index.json /dest/active_index.json
    echo "migrated RAG formal storage into /dest"
  '

echo "旧目录未删除；确认新卷内容正确后再自行清理。"
