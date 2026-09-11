#!/usr/bin/env bash
set -u

getenv() {
  printenv "$1" 2>/dev/null || true
}

main_url=$(getenv MAIN_LLM_BASE_URL)
memory_llm_url=$(getenv MEMORY_LLM_BASE_URL)
embedding_url=$(getenv EMBEDDING_BASE_URL)
hindsight_url=$(getenv HINDSIGHT_BASE_URL)
memory_url=$(getenv XIAOWU_MEMORY_BASE_URL)

[ -n "$main_url" ] || main_url=http://127.0.0.1:10000/v1
[ -n "$memory_llm_url" ] || memory_llm_url=http://127.0.0.1:10002/v1
[ -n "$embedding_url" ] || embedding_url=http://127.0.0.1:10001/v1
[ -n "$hindsight_url" ] || hindsight_url=http://127.0.0.1:8888
[ -n "$memory_url" ] || memory_url=http://127.0.0.1:8890

fail=0

check_json() {
  label=$1
  url=$2
  if curl -fsS --max-time 10 "$url" >/dev/null; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label" >&2
    fail=1
  fi
}

check_json main-llm "$main_url/models"
check_json memory-llm "$memory_llm_url/models"
check_json embedding "$embedding_url/models"
check_json hindsight "$hindsight_url/health"
check_json memory-service "$memory_url/health"

exit "$fail"
