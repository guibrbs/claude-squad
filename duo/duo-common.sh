#!/bin/bash -p
# Shared helpers for the Claude Squad convenience tools (installer, updater,
# update check). They call the versioned toolset; they never edit apps or
# profiles directly. External commands are absolute paths on purpose.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077

OSASCRIPT=/usr/bin/osascript
OPEN=/usr/bin/open
PS=/bin/ps
PLISTBUDDY=/usr/libexec/PlistBuddy
SLEEP=/bin/sleep

# The product is Claude Squad; Claude Duo stays the name of its first copy,
# and the installed paths keep their claude-duo names.
DUO_PRODUCT="Claude Squad"
DUO_HOME="$HOME/.local/share/claude-duo"
DUO_CONFIG_POINTER="$DUO_HOME/config-path"
DUO_LOG_DIR="$HOME/Library/Logs/Claude Duo Updater"
DUO_SOURCE_APP="/Applications/Claude.app"
DUO_DISPATCHER="$HOME/.local/share/claude-instances/toolsets/dispatcher"
DUO_QUIT_TIMEOUT=60
# Dialogs and notifications follow the macOS language: Portuguese when the
# first preferred language is Portuguese, English otherwise.
DUO_LANGUAGE="$(/usr/bin/defaults read -g AppleLanguages 2>/dev/null | /usr/bin/awk -F'"' 'NF>1{print $2; exit}')"

# duo_t ENGLISH PORTUGUESE
duo_t() { case "$DUO_LANGUAGE" in pt*) printf '%s' "$2" ;; *) printf '%s' "$1" ;; esac; }

duo_lower() { printf '%s' "$1" | LC_ALL=C /usr/bin/tr '[:upper:]' '[:lower:]'; }

duo_error() { printf 'claude-duo: %s\n' "$*" >&2; }

# Text for AppleScript string literals: backslashes and quotes escaped.
duo_as_text() { local text="${1//\\/\\\\}"; printf '%s' "${text//\"/\\\"}"; }

duo_notify() {
    "$OSASCRIPT" -e "display notification \"$(duo_as_text "$2")\" with title \"$(duo_as_text "$1")\"" >/dev/null 2>&1 || true
}

# Prints the chosen button; empty when dismissed or unavailable.
duo_ask() {
    local message="$1" first="$2" second="$3"
    "$OSASCRIPT" -e "button returned of (display alert \"$DUO_PRODUCT\" message \"$(duo_as_text "$message")\" buttons {\"$(duo_as_text "$second")\", \"$(duo_as_text "$first")\"} default button \"$(duo_as_text "$first")\")" 2>/dev/null || true
}

duo_config() {
    local path
    [ -f "$DUO_CONFIG_POINTER" ] && [ ! -L "$DUO_CONFIG_POINTER" ] || { duo_error "$DUO_PRODUCT is not installed (run duo/install-duo.sh)"; return 1; }
    IFS= read -r path < "$DUO_CONFIG_POINTER" || true
    case "$path" in "$HOME"/.config/claude-instances/*.conf) ;; *) duo_error 'unexpected config location'; return 1 ;; esac
    [ -f "$path" ] && [ ! -L "$path" ] || { duo_error "missing config: $path"; return 1; }
    printf '%s\n' "$path"
}

duo_trim() { local value="${1#"${1%%[![:space:]]*}"}"; printf '%s' "${value%"${value##*[![:space:]]}"}"; }
duo_expand_home() { case "$1" in '~/'*) printf '%s/%s' "$HOME" "${1#\~/}" ;; *) printf '%s' "$1" ;; esac; }

# The managed copies (Duo, Trio...) as the config lists them: DUO_NAMES,
# their apps in DUO_APPS and their CLAUDE_CONFIG_DIR in DUO_CODE_DIRS (empty
# when the line sets none). Only reads; the toolset validates the config.
duo_load_copies() {
    local config line name _bundle _desktop _hue env pair code apps=/Applications pairs
    config="${1:-}"; [ -n "$config" ] || config="$(duo_config)" || return 1
    DUO_NAMES=(); DUO_APPS=(); DUO_CODE_DIRS=()
    while IFS= read -r line || [ -n "$line" ]; do
        line="$(duo_trim "${line%%#*}")"
        case "$line" in '') continue ;; '@apps user') apps="$HOME/Applications"; continue ;; @*) continue ;; esac
        IFS='|' read -r name _bundle _desktop _hue env <<<"$line"
        name="$(duo_trim "$name")"
        [[ "$name" =~ ^[A-Za-z][A-Za-z0-9_-]{0,63}$ ]] || { duo_error "invalid copy name in $config"; return 1; }
        code=""
        IFS=';' read -ra pairs <<<"${env:-}"
        for pair in "${pairs[@]-}"; do
            pair="$(duo_trim "$pair")"
            case "$pair" in CLAUDE_CONFIG_DIR=*) code="$(duo_expand_home "$(duo_trim "${pair#CLAUDE_CONFIG_DIR=}")")" ;; esac
        done
        DUO_NAMES+=("$name"); DUO_CODE_DIRS+=("$code")
        DUO_APPS+=("$apps/Claude $name.app")
    done < "$config"
    [ "${#DUO_NAMES[@]}" -gt 0 ] || { duo_error "no copy in $config"; return 1; }
}

# "Claude Duo", "Claude Duo and Claude Trio"... in the macOS language; with
# --pt-article the Portuguese names take "o" ("o Claude Duo e o Claude Trio").
# Names every copy, or only the copies given after the option.
duo_label() {
    local article="" i text="" last names
    [ "${1:-}" != --pt-article ] || { article="$(duo_t "" "o ")"; shift; }
    if [ "$#" -gt 0 ]; then names=("$@"); else names=("${DUO_NAMES[@]}"); fi
    last=$((${#names[@]} - 1))
    for i in "${!names[@]}"; do
        if [ "$i" -gt 0 ]; then
            if [ "$i" -eq "$last" ]; then text="$text$(duo_t " and " " e ")"; else text="$text, "; fi
        fi
        text="$text${article}Claude ${names[$i]}"
    done
    printf '%s' "$text"
}

duo_refresh() {
    local config
    config="$(duo_config)" || return 1
    /usr/bin/env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" PATH="$PATH" LANG=en_US.UTF-8 \
        CLAUDE_INSTANCES_CONF="$config" "$DUO_DISPATCHER" run claude-apps-refresh.sh "$@"
}

# Fails, printing nothing, when the app is not built: PlistBuddy alone would
# print "File Doesn't Exist, Will Create" to stdout.
duo_version() { [ -f "$1/Contents/Info.plist" ] && "$PLISTBUDDY" -c 'Print :CFBundleShortVersionString' "$1/Contents/Info.plist" 2>/dev/null; }

# Prints "PID executable" for the app and its Electron helpers. Helpers in
# Contents/Helpers are started by other programs (chrome-native-host by
# Chrome, disclaimer by the claude CLI) and outlive the app; as in the native
# desktop-idle check (bin/claude-maintenance.h), only executables in a
# Contents/MacOS count. With "any", every executable inside the bundle counts,
# as in the builder's check of the app it replaces (app-state): Electron's
# chrome_crashpad_handler, in a framework's Helpers folder, detaches from the
# app and lives on for a few seconds after it quits.
duo_app_processes() {
    local prefix="$1/Contents/" scope="${2:-}" pid executable
    while read -r pid executable; do
        case "$scope:$executable" in
            ":$prefix"MacOS/*|":$prefix"*/Contents/MacOS/*|"any:$prefix"*) printf '%s %s\n' "$pid" "$executable" ;;
        esac
    done < <("$PS" -axo pid=,comm=)
}

duo_app_running() { [ -n "$(duo_app_processes "$1")" ]; }

# True when this script descends from the app, e.g. run from its Code tab:
# quitting the app to build would also end the caller. A claude CLI in a
# terminal only adds Contents/Helpers/disclaimer to the chain, which does
# not count, so CLAUDECODE alone is not used.
duo_inside_app() {
    local prefix="$1/Contents/" pid=$$ ppid executable depth=0
    while [ "$depth" -lt 64 ]; do
        read -r ppid executable < <("$PS" -o ppid=,comm= -p "$pid") || return 1
        case "$executable" in "$prefix"MacOS/*|"$prefix"*/Contents/MacOS/*) return 0 ;; esac
        case "$ppid" in ''|*[!0-9]*|0|1) return 1 ;; esac
        pid="$ppid"; depth=$((depth + 1))
    done
    return 1
}

duo_quit_app() {
    local app="$1" identifier
    duo_app_running "$app" || return 0
    identifier="$("$PLISTBUDDY" -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist" 2>/dev/null)" || return 1
    "$OSASCRIPT" -e "tell application id \"$(duo_as_text "$identifier")\" to quit" >/dev/null 2>&1 || true
}

# True while the original's main executable runs, or anything from a copy's
# bundle: the builder refuses to replace a copy while any of its executables does.
duo_still_running() {
    local app
    duo_app_running "$DUO_SOURCE_APP" && return 0
    for app in "${DUO_APPS[@]}"; do [ -z "$(duo_app_processes "$app" any)" ] || return 0; done
    return 1
}

# Asks the original and every copy to quit (they may show their own save
# prompts) and waits: the build needs all Claude Desktop apps closed.
duo_quit_all() {
    local waited=0 app
    for app in "$DUO_SOURCE_APP" "${DUO_APPS[@]}"; do duo_quit_app "$app"; done
    while duo_still_running; do
        [ "$waited" -lt "$DUO_QUIT_TIMEOUT" ] || return 1
        "$SLEEP" 1
        waited=$((waited + 1))
    done
}
