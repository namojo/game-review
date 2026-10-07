#!/usr/bin/env bash
# gen_keyart.sh — codex exec 의 image_generation 으로 키아트를 최대 5장 병렬 생성한다.
# codex-image 스킬의 배치 방식에 '화풍 참고 이미지 첨부(-i)'를 더한 래퍼다.
#
# Usage:
#   gen_keyart.sh <out_dir> <ref1.png,ref2.png|-> "프롬프트1::keyart_16x9.png" "프롬프트2::keyart_9x16.png" ...
#   (참고 이미지가 없으면 두 번째 인자에 - )
#
# 결과: <out_dir>/<파일명>, 로그: <out_dir>/.codex-logs/<파일명>.md
#
# 모델: codex 전역 설정(~/.codex/config.toml)의 기본 모델이 ChatGPT 로그인에서 지원되지 않으면
#   "model is not supported when using Codex with a ChatGPT account" 로 실패한다(2026-10-07 관찰:
#   gpt-6.1-sol 실패, gpt-6-sol 성공). 사용자 전역 설정을 바꾸지 말고 CODEX_MODEL 로 이 실행만 지정한다.
#   CODEX_MODEL=gpt-6-sol gen_keyart.sh ...
set -u
MAX_PARALLEL=5
[ $# -lt 3 ] && { sed -n 2,9p "$0"; exit 1; }
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"; shift
REFS="$1"; shift
LOG="$OUT/.codex-logs"; mkdir -p "$LOG"

command -v codex >/dev/null || { echo "[error] codex CLI 없음" >&2; exit 3; }
codex login status >/dev/null 2>&1 || { echo "[error] codex 미로그인: codex login" >&2; exit 4; }

IMG_ARGS=()
if [ "$REFS" != "-" ]; then
  IFS=',' read -ra R <<< "$REFS"
  for r in "${R[@]}"; do
    [ -f "$r" ] || { echo "[error] 참고 이미지 없음: $r" >&2; exit 5; }
    IMG_ARGS+=(--image "$(cd "$(dirname "$r")" && pwd)/$(basename "$r")")
  done
fi

run_one() {
  local prompt="$1" file="$2"
  local full="이미지 생성 도구(image_generation)로 이미지를 1장 생성하고 작업 폴더의 ./${file} 로 저장하라. 첨부 이미지가 있으면 그 화풍만 참고하고, 첨부 이미지를 편집하거나 그대로 복사하지 않는다. 이미지 안에 글자·숫자·로고·워터마크를 절대 넣지 않는다. 저장 후 파일 경로만 한 줄로 보고하라.

[이미지 요구사항]
${prompt}"
  # 이전 결과가 남아 있으면 실패해도 성공처럼 보인다 → 시작 시각 표식을 두고 그보다 새 파일만 성공으로 본다
  local mark="$LOG/.start_${file}"; : > "$mark"
  local model_args=(); [ -n "${CODEX_MODEL:-}" ] && model_args=(-m "$CODEX_MODEL")
  codex exec --sandbox workspace-write --skip-git-repo-check --cd "$OUT" ${model_args[@]+"${model_args[@]}"} \
    ${IMG_ARGS[@]+"${IMG_ARGS[@]}"} -o "$LOG/${file}.md" -- "$full" >"$LOG/${file}.stdout" 2>&1
  if [ -s "$OUT/$file" ] && [ "$OUT/$file" -nt "$mark" ] && file "$OUT/$file" | grep -qiE "image|PNG|JPEG"; then
    echo "[ok] $file $(file -b "$OUT/$file" | cut -d, -f2)"
  elif grep -q "not supported when using Codex with a ChatGPT account" "$LOG/${file}.stdout"; then
    echo "[fail] $file — 기본 모델이 ChatGPT 로그인에서 지원되지 않음. CODEX_MODEL 로 지원 모델을 지정해 다시 실행 (로그: $LOG/${file}.stdout)"
  else
    echo "[fail] $file — 새 이미지가 만들어지지 않음 (로그: $LOG/${file}.stdout)"
  fi
  rm -f "$mark"
}

items=("$@"); n=${#items[@]}; i=0
while [ $i -lt $n ]; do
  pids=()
  for ((j=0; j<MAX_PARALLEL && i<n; j++, i++)); do
    it="${items[$i]}"; p="${it%%::*}"; f="${it#*::}"
    run_one "$p" "$f" & pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
done
