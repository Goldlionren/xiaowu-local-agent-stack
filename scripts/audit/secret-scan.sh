#!/usr/bin/env bash
set -u

root=.
[ "$#" -eq 0 ] || root=$1
fail=0

scan() {
  label=$1
  pattern=$2
  if rg -n --hidden --glob '!.git/**' --glob '!scripts/audit/secret-scan.sh' -- "$pattern" "$root"; then
    printf 'FAIL %s\n' "$label" >&2
    fail=1
  else
    printf 'PASS %s\n' "$label"
  fi
}

scan private-key '-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
scan telegram-token '[0-9]{8,12}:[A-Za-z0-9_-]{30,}'
scan common-api-token '(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})'
scan bearer-value 'Bearer[[:space:]]+[A-Za-z0-9._~-]{20,}'
scan credential-url '(postgres|postgresql|mysql|redis)://[^[:space:]@:]+:[^[:space:]@]+@'
scan private-ip '(^|[^0-9])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})([^0-9]|$)'
scan personal-home '/home/[A-Za-z0-9._-]+/'
scan telegram-id '(chat[_-]?id|user[_-]?id|telegram[_-]?destination)[^0-9]{0,24}[0-9]{7,}'

if find "$root" -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name 'credentials.json' -o -name 'auth.json' -o -name '*.dump' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.gguf' -o -name '*.safetensors' -o -name '*.ckpt' \) -print | rg .; then
  printf 'FAIL prohibited file type\n' >&2
  fail=1
else
  printf 'PASS prohibited file types\n'
fi

exit "$fail"
