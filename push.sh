#!/usr/bin/env bash
# Публикация текущих изменений в GitHub (origin/<текущая ветка>).
# Спрашивает только название коммита, дальше делает всё сам:
# add -A, commit, pull --rebase (подтянуть чужие изменения без слияний-мусора), push.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Не git-репозиторий: $(pwd)" >&2
  exit 1
fi

branch="$(git symbolic-ref --short -q HEAD || true)"
if [ -z "$branch" ]; then
  echo "HEAD не на ветке (detached) — переключись на ветку перед пушем." >&2
  exit 1
fi

if [ -z "$(git status --porcelain)" ]; then
  echo "Нет изменений — нечего коммитить."
  exit 0
fi

echo "Изменения:"
git status -s
echo

read -rp "Название коммита: " msg
while [ -z "$msg" ]; do
  read -rp "Название коммита не может быть пустым, повтори: " msg
done

git add -A
git commit -m "$msg"

echo "Синхронизирую с origin/$branch…"
if git remote get-url origin >/dev/null 2>&1 && git ls-remote --exit-code origin "$branch" >/dev/null 2>&1; then
  git pull --rebase origin "$branch"
fi

git push origin "$branch"
echo "Готово: запушено в origin/$branch."
