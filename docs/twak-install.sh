#!/usr/bin/env bash
# install.sh — Trust Wallet Agent Kit installer
# One-line install:  curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash

# POSIX guard — must run before any bash-only syntax below.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This installer requires bash. Re-run with:" >&2
  echo "  curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash" >&2
  exit 1
fi

set -euo pipefail
IFS=$'\n\t'

# ─── Constants ───────────────────────────────────────────────────────────────

PACKAGE_NAME="@trustwallet/cli"
MIN_NODE_MAJOR=22
MIN_NODE_MINOR=14

# ─── Flags & env vars ────────────────────────────────────────────────────────

NO_ONBOARD="${TWAK_NO_ONBOARD:-0}"
VERSION="${TWAK_VERSION:-latest}"

# ─── Colour handling ─────────────────────────────────────────────────────────

# Disable colours when NO_COLOR is set or stdout isn't a TTY (e.g. piped to a log).
if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
  C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_DIM=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_RESET=''
fi

# ─── UI helpers ──────────────────────────────────────────────────────────────

ui_info()    { printf "%s·%s %s\n"  "$C_DIM"    "$C_RESET" "$*"; }
ui_success() { printf "%s✓%s %s\n"  "$C_GREEN"  "$C_RESET" "$*"; }
ui_warn()    { printf "%s!%s %s\n"  "$C_YELLOW" "$C_RESET" "$*" >&2; }
ui_error()   { printf "%s✗%s %s\n"  "$C_RED"    "$C_RESET" "$*" >&2; }

print_banner() {
  printf "\n%s⛓  Trust Wallet Agent Kit — Installer%s\n\n" "$C_BOLD" "$C_RESET"
}

# ─── Argument parsing ────────────────────────────────────────────────────────

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-onboard)
        NO_ONBOARD=1; shift ;;
      --version)
        # Reject "--version" with no value and "--version --other-flag" (which
        # would silently consume the next flag as the version string).
        if [[ $# -lt 2 || "$2" == --* ]]; then
          ui_error "--version requires a value"
          exit 1
        fi
        VERSION="$2"; shift 2 ;;
      -h|--help)
        cat <<EOF
Usage: install.sh [--no-onboard] [--version <v>]

Flags:
  --no-onboard       Install CLI, skip 'twak setup'
  --version <v>      Pin to a specific @trustwallet/cli version (default: latest)
  -h, --help         Show this help

Environment:
  TWAK_NO_ONBOARD=1      Same as --no-onboard
  TWAK_VERSION=<v>       Same as --version <v>
  NO_PROMPT=1            Non-interactive mode (passed to 'twak setup')
  NO_COLOR=1             Disable ANSI colours
  TWAK_INSTALL_DEBUG=1   Enable 'set -x' for debugging
EOF
        exit 0 ;;
      *)
        ui_error "Unknown argument: $1"
        printf "  Run 'install.sh --help' for usage.\n" >&2
        exit 1 ;;
    esac
  done
}

# ─── Platform detection ──────────────────────────────────────────────────────

detect_platform() {
  case "$(uname -s 2>/dev/null)" in
    Darwin)
      OS="macos" ;;
    Linux)
      # WSL sets $WSL_DISTRO_NAME; distinguish it from bare Linux for the label.
      if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        OS="linux-wsl"
      else
        OS="linux"
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*)
      ui_error "Native Windows is not supported. Use WSL."
      exit 3 ;;
    *)
      ui_error "Unsupported OS: $(uname -s). Supported: macOS, Linux, WSL."
      exit 3 ;;
  esac

  ARCH="$(uname -m 2>/dev/null)"
  case "$OS" in
    macos)     PLATFORM_LABEL="macOS $ARCH" ;;
    linux-wsl) PLATFORM_LABEL="Linux $ARCH (WSL: $WSL_DISTRO_NAME)" ;;
    linux)     PLATFORM_LABEL="Linux $ARCH" ;;
  esac
  printf "%s·%s %-10s %s\n" "$C_DIM" "$C_RESET" "Platform" "$PLATFORM_LABEL"
}

# ─── Node check ──────────────────────────────────────────────────────────────

check_node() {
  if ! command -v node >/dev/null 2>&1; then
    ui_error "Node ${MIN_NODE_MAJOR}.${MIN_NODE_MINOR}+ required (not installed)."
    cat <<EOF >&2

  Install Node:
    macOS:  brew install node
    Linux:  https://nodejs.org or your package manager
    Other:  https://nodejs.org

  Then re-run:
    curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash
EOF
    exit 4
  fi

  local v major minor
  v="$(node --version 2>/dev/null | sed 's/^v//')"
  major="$(printf '%s' "$v" | cut -d. -f1)"
  minor="$(printf '%s' "$v" | cut -d. -f2)"

  # Non-numeric version components would error the arithmetic compare under set -e.
  if ! [[ "$major" =~ ^[0-9]+$ ]] || ! [[ "$minor" =~ ^[0-9]+$ ]]; then
    ui_error "Could not parse Node version: '$v' (expected MAJOR.MINOR.PATCH)."
    exit 4
  fi

  # major < MIN  OR  (major == MIN AND minor < MIN_MINOR)
  if [[ "$major" -lt "$MIN_NODE_MAJOR" ]] || \
     { [[ "$major" -eq "$MIN_NODE_MAJOR" ]] && [[ "$minor" -lt "$MIN_NODE_MINOR" ]]; }; then
    ui_error "Node ${MIN_NODE_MAJOR}.${MIN_NODE_MINOR}+ required (you have v${v})."
    printf "  Update via your version manager, then re-run.\n" >&2
    exit 4
  fi
  printf "%s·%s %-10s %s %s✓%s\n" "$C_DIM" "$C_RESET" "Node" "v$v" "$C_GREEN" "$C_RESET"
}

# ─── npm check ───────────────────────────────────────────────────────────────

check_npm() {
  if ! command -v npm >/dev/null 2>&1; then
    ui_error "npm is required but not on PATH. Reinstall Node or fix PATH."
    exit 5
  fi
  local v
  v="$(npm --version 2>/dev/null)"
  printf "%s·%s %-10s %s %s✓%s\n" "$C_DIM" "$C_RESET" "npm" "v$v" "$C_GREEN" "$C_RESET"
}

# ─── Install ─────────────────────────────────────────────────────────────────

install_cli() {
  # Fast-path: resolve "latest" → concrete version, skip install if it matches.
  # `|| true` + 15s fetch-timeout so a flaky network falls through, not hangs.
  local target current
  target="$(npm view --fetch-timeout=15000 "${PACKAGE_NAME}@${VERSION}" version 2>/dev/null || true)"
  if command -v twak >/dev/null 2>&1; then
    current="$(twak --version 2>/dev/null | head -n1 || true)"
    if [[ -n "$target" ]] && [[ "$current" == "$target" ]]; then
      printf "%s·%s %-10s %sv%s%s already installed at %s\n" \
        "$C_DIM" "$C_RESET" "twak" "$C_BOLD" "$current" "$C_RESET" "$(command -v twak)"
      return 0
    fi
  fi

  printf "\n%s·%s Installing %s@%s...\n" "$C_DIM" "$C_RESET" "$PACKAGE_NAME" "$VERSION"

  # Direct pipeline (no $(…)) keeps PIPESTATUS[0] unambiguously tied to npm.
  local out exit_code tmp
  tmp="$(mktemp)"
  set +e
  npm install -g "${PACKAGE_NAME}@${VERSION}" 2>&1 | sed 's/^/    /' | tee "$tmp"
  exit_code=${PIPESTATUS[0]}
  set -e
  out="$(<"$tmp")"
  rm -f "$tmp"

  if [[ "$exit_code" -ne 0 ]]; then
    if printf '%s' "$out" | grep -qiE "EACCES|permission denied"; then
      ui_error "Install failed — permissions issue with the global npm prefix."
      cat <<'EOF' >&2

  npm's global directory is owned by root. Recommended fixes (no sudo needed):

    1. Use a Node version manager — nvm, fnm, or volta — which installs node
       and the global prefix into your home directory.

    2. Or, point npm at a user-owned prefix:
         mkdir -p ~/.npm-global
         npm config set prefix ~/.npm-global
         export PATH="$HOME/.npm-global/bin:$PATH"   # add to ~/.zshrc or ~/.bashrc
       Then re-run the install command.

  See: https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally
EOF
    else
      ui_error "npm install failed (exit $exit_code)."
    fi
    exit 6
  fi
}

# ─── Post-install verify ─────────────────────────────────────────────────────

verify_install() {
  # Catches the case where npm installed to a directory that isn't on PATH
  # (e.g., a custom NPM_CONFIG_PREFIX that the user never added to their rc).
  if ! command -v twak >/dev/null 2>&1; then
    local npm_prefix
    npm_prefix="$(npm prefix -g 2>/dev/null || echo "<unknown>")"
    ui_error "Install succeeded but 'twak' not on PATH."
    printf "  Your npm bin directory (%s/bin) may not be on PATH.\n" "$npm_prefix" >&2
    printf "  Add it to your shell rc, open a new shell, then re-run the install command.\n" >&2
    exit 7
  fi
  local v path
  v="$(twak --version 2>/dev/null | head -n1)"
  path="$(command -v twak)"
  printf "%s·%s %-10s %s installed at %s\n" "$C_DIM" "$C_RESET" "twak" "v$v" "$path"
}

# ─── Exec setup ──────────────────────────────────────────────────────────────

exec_setup() {
  if [[ "$NO_ONBOARD" == "1" ]]; then
    printf "\n"
    ui_success "Installed. To finish setup later:"
    printf "    twak setup\n\n"
    exit 0
  fi

  # Forward NO_PROMPT explicitly so twak setup picks it up across `exec`.
  if [[ -n "${NO_PROMPT:-}" ]]; then export NO_PROMPT; fi

  printf "\n%s→%s %sContinuing to setup...%s\n\n" "$C_GREEN" "$C_RESET" "$C_BOLD" "$C_RESET"

  # Under `curl | bash`, stdin is closed; /dev/tty still reaches the user's
  # terminal so twak can prompt. Headless envs (CI) have no /dev/tty.
  if [[ -e /dev/tty ]]; then
    exec twak setup </dev/tty
  else
    exec twak setup
  fi
}

# ─── main ────────────────────────────────────────────────────────────────────

main() {
  if [[ "${TWAK_INSTALL_DEBUG:-}" == "1" ]]; then
    set -x
  fi
  parse_args "$@"
  print_banner
  detect_platform
  check_node
  check_npm
  install_cli
  verify_install
  exec_setup
}

main "$@"
