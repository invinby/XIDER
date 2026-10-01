#!/usr/bin/env bash
set -eu

state_file="${XIDER_TEST_SYSTEMCTL_STATE:?}"
case "${1:-}" in
  is-enabled|daemon-reload) : ;;
  stop) : >"${state_file}" ;;
  is-active)
    if [[ "${XIDER_TEST_FAIL_HEALTH:-0}" == "1" && -f "${state_file}" ]]; then
      exit 3
    fi
    :
    ;;
  show)
    if [[ "${XIDER_TEST_FAIL_HEALTH:-0}" == "1" && -f "${state_file}" ]]; then
      printf '0\n'
    else
      printf '2468\n'
    fi
    ;;
  restart)
    if [[ "${XIDER_TEST_FAIL_HEALTH:-0}" != "1" ]]; then
      rm -f -- "${state_file}"
    else
      : >"${state_file}"
    fi
    ;;
  *)
    echo "unexpected mocked systemctl command: $*" >&2
    exit 64
    ;;
esac
