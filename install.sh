#!/bin/zsh
# Install AirQuote: checks this Mac, sets up Python, downloads a speech model
# that suits it, builds /Applications/AirQuote.app and opens the permission
# pages. Safe to run again: finished steps are skipped.
#
#   ./install.sh              ask whether to add speaker separation
#   ./install.sh --speakers   also install speaker separation (~1 GB)
#   ./install.sh --no-speakers
#   ./install.sh --check      only check this Mac, change nothing

set -euo pipefail

HERE="${0:A:h}"
cd "$HERE"

SPEAKERS="ask"
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --speakers) SPEAKERS="yes" ;;
    --no-speakers) SPEAKERS="no" ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) print -u2 "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

bold() { print -P "%B$1%b"; }
ok()   { print -P "  %F{green}✓%f $1"; }
warn() { print -P "  %F{yellow}!%f $1"; }
fail() { print -P "  %F{red}✗%f $1"; exit 1; }

bold "Checking this Mac"

[[ "$(uname -m)" == "arm64" ]] || fail "AirQuote needs an Apple Silicon Mac (M1 or newer); MLX does not run on Intel."
ok "Apple Silicon ($(sysctl -n machdep.cpu.brand_string), $(( $(sysctl -n hw.memsize) / 1073741824 )) GB memory)"

macos="$(sw_vers -productVersion)"
[[ "${macos%%.*}" -ge 13 ]] || fail "macOS 13 or newer is needed (this Mac has $macos)."
ok "macOS $macos"

# macOS shields these folders (TCC): the app could not read its own code there
# and would fail with "Operation not permitted", while Terminal works fine.
case "$HERE" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|"$HOME/Library/CloudStorage"*|"$HOME/Library/Mobile Documents"*)
    fail "This folder is protected by macOS: $HERE
     Move the folder first, for example:  mv \"$HERE\" ~/Developer/airquote" ;;
esac
ok "Install folder: $HERE"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 \
                 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
[[ -n "$PYTHON" ]] || fail "Python 3.11 or newer is needed. Install it with:  brew install python@3.13"
ok "Python $("$PYTHON" -c 'import platform; print(platform.python_version())') ($PYTHON)"

if ! command -v brew >/dev/null 2>&1; then
  fail "Homebrew is needed for ffmpeg. Install it from https://brew.sh and run this script again."
fi
ok "Homebrew"

if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg"
elif (( CHECK_ONLY )); then
  warn "ffmpeg is missing (the installer will add it with Homebrew)"
else
  print "  Installing ffmpeg with Homebrew…"
  brew install ffmpeg
  ok "ffmpeg"
fi

if command -v ollama >/dev/null 2>&1; then
  ok "Ollama found (needed only for the → Dutch, Feedback and Rewrite modes)"
else
  warn "Ollama not found: dictation works, the → Dutch / Feedback / Rewrite modes paste plain text"
fi

if (( CHECK_ONLY )); then
  print; bold "This Mac can run AirQuote. Run ./install.sh to install."
  exit 0
fi

if [[ "$SPEAKERS" == "ask" ]]; then
  print
  if read -q "?Also install speaker separation for the Recordings tab (about 1 GB extra)? [y/N] "; then
    SPEAKERS="yes"
  else
    SPEAKERS="no"
  fi
  print
fi

print; bold "Setting up Python"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
if [[ "$SPEAKERS" == "yes" ]]; then
  .venv/bin/python -m pip install --quiet -r requirements.txt
  ok "Python packages, including speaker separation"
else
  grep -v '^pyannote' requirements.txt > .venv/requirements-core.txt
  .venv/bin/python -m pip install --quiet -r .venv/requirements-core.txt
  ok "Python packages (speaker separation skipped; add it later with ./install.sh --speakers)"
fi

print; bold "Your settings"
USER_CONFIG="$HOME/.config/praattypen/config.yaml"
if [[ -f "$USER_CONFIG" ]]; then
  ok "Keeping your existing settings: $USER_CONFIG"
else
  mkdir -p "${USER_CONFIG:h}"
  cp config.yaml "$USER_CONFIG"
  ok "Created $USER_CONFIG (edit it, or use the Models tab)"
fi

print; bold "Speech model"
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, ".")
from praattypen.config import load_config, default_config_path
from praattypen.models_catalog import resolve_whisper_model, system_info, recommend
info = system_info()
model = resolve_whisper_model(load_config(default_config_path()).models.whisper, info)
print(f"  Recommended for {info.summary()}: {recommend(info)}")
if model.startswith("/"):
    print(f"  ✓ Using the copy already on this Mac: {model}")
else:
    from huggingface_hub import snapshot_download
    print(f"  Downloading {model} (only the first time)…")
    snapshot_download(model)
    print("  ✓ Model ready")
PY

print; bold "Building the app"
if [[ -d /Applications/AirQuote.app ]]; then
  warn "Rebuilding AirQuote.app: macOS will ask for its permissions again."
  osascript -e 'quit app "AirQuote"' >/dev/null 2>&1 || true
  sleep 1
fi
./scripts/build_app.sh >/dev/null
ok "/Applications/AirQuote.app"

open /Applications/AirQuote.app
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
sleep 1
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

print
bold "Almost done: three permissions"
cat <<'TXT'
  System Settings is open. Switch AirQuote on in:
    1. Input Monitoring   (so it sees the hotkey)
    2. Accessibility      (so it can paste)
  Not listed? Click +, choose Applications → AirQuote.

  Then quit AirQuote (right-click its Dock icon → Quit) and open it again.
  Hold Right Option ⌥, speak, let go. Click Allow when macOS asks about the
  microphone. Double-tap Right Option for hands-free.
TXT
if [[ "$SPEAKERS" == "yes" ]]; then
  cat <<'TXT'

  Speaker separation, one time only: accept the model's terms at
    https://huggingface.co/pyannote/speaker-diarization-community-1
  (free account), then run:  .venv/bin/hf auth login
TXT
fi
if ! command -v ollama >/dev/null 2>&1; then
  cat <<'TXT'

  Optional, for → Dutch, Feedback and Rewrite:  brew install ollama
  then:  ollama pull gemma4:12b   (about 8 GB; use a smaller model on 8-16 GB Macs
  and set assistant_model in ~/.config/praattypen/config.yaml)
TXT
fi
