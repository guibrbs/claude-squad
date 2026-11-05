#!/bin/bash -p
# Removes Claude Squad: every managed copy (Claude Duo, and any other the
# config lists), the updater app, the update check, the Duo tools, config and
# logs, plus the shared toolset and retained build transactions when no other
# config in ~/.config/claude-instances uses them. Everything is moved to one
# folder in the Trash, never deleted, and the list is shown before asking.
# The copies' account data (Desktop and Code profiles) and Keychain items stay.
#   --only NAME              remove just the copy "Claude NAME": its app, and
#                            its line (and @links, when it names it) from the
#                            config; the other copies and the tools stay
#   --include-account-data   also move the Desktop and Code profiles that the
#                            removed copies' lines name (by default
#                            ~/Library/Application Support/Claude-Duo and
#                            ~/.claude-duo for the Duo), to the Trash
#   --yes                    do not ask before moving
DUO_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
source "$DUO_TOOLS/duo-common.sh" || exit 1
set -u
LAUNCHCTL=/bin/launchctl
AGENT_LABEL=io.github.guibrbs.claude-duo.update-check
AGENT="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
UPDATER_APP="$HOME/Applications/Update Claude Squad.app"
LEGACY_UPDATER_APPS=("$HOME/Applications/Update Claude Duo.app" "$HOME/Applications/Atualizar Claude Duo.app")
CONFIGS="$HOME/.config/claude-instances"
TOOLSET="$HOME/.local/share/claude-instances"

account=no; ask=yes; only=""
fail() { duo_error "$*"; exit 1; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --include-account-data) account=yes ;;
        --yes) ask=no ;;
        --only) [ "$#" -ge 2 ] || { duo_error '--only needs a name'; exit 2; }; only="$2"; shift ;;
        *) duo_error "unknown option: $1"; exit 2 ;;
    esac
    shift
done

[ "$(/usr/bin/id -u)" != 0 ] || fail 'run as your own user, not as root'

config="$(duo_config 2>/dev/null)" || config="$CONFIGS/claude-duo.conf"
# The copies to remove: all of them, or the one --only names.
DUO_NAMES=(Duo); DUO_APPS=("$HOME/Applications/Claude Duo.app")
[ ! -f "$config" ] || [ -L "$config" ] || duo_load_copies "$config" 2>/dev/null || true
if [ -n "$only" ]; then
    picked=""
    for i in "${!DUO_NAMES[@]}"; do [ "$(duo_lower "${DUO_NAMES[$i]}")" != "$(duo_lower "$only")" ] || picked="$i"; done
    [ -n "$picked" ] || fail "Claude $only is not in $config"
    [ "${#DUO_NAMES[@]}" -gt 1 ] || fail "Claude ${DUO_NAMES[$picked]} is the only copy: run without --only to remove $DUO_PRODUCT entirely"
    DUO_NAMES=("${DUO_NAMES[$picked]}"); DUO_APPS=("${DUO_APPS[$picked]}")
fi
for i in "${!DUO_APPS[@]}"; do
    ! duo_app_running "${DUO_APPS[$i]}" || fail "quit Claude ${DUO_NAMES[$i]} and try again"
done
removing() { local name; for name in "${DUO_NAMES[@]}"; do [ "$(duo_lower "$name")" != "$(duo_lower "$1")" ] || return 0; done; return 1; }
others=no
for file in "$CONFIGS"/*.conf; do
    [ -e "$file" ] && [ "$file" != "$config" ] && others=yes
done

trim() { duo_trim "$1"; }
expand_home() { duo_expand_home "$1"; }
# True for the Duo profile locations the toolset accepts for a managed copy
# (bin/claude-instances-common.sh): a flat Claude-* or .claude-* folder, or a
# managed UUID profile. The original's own folders never match.
is_duo_profile() {
    local kind="$1" path="$2" leaf
    case "$path/" in *'/../'*|*'/./'*|*'//'*) return 1 ;; esac
    [[ "$path" != *[[:cntrl:]]* ]] || return 1
    case "$kind:$path" in
        "$kind:$PROFILES/"*) [[ "${path#"$PROFILES/"}" =~ ^$UUID/$kind$ ]]; return ;;
        "desktop:$HOME/Library/Application Support/Claude-"*) leaf="${path#"$HOME/Library/Application Support/Claude-"}" ;;
        "code:$HOME/.claude-"*) leaf="${path#"$HOME/.claude-"}" ;;
        *) return 1 ;;
    esac
    [ -n "$leaf" ] && [[ "$leaf" != */* ]]
}
PROFILES="$HOME/Library/Application Support/Multi Claude Desktop/Profiles"
UUID='[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}'
# The account data is where each removed copy's line says: its Desktop
# profile (third column) and its CLAUDE_CONFIG_DIR. The defaults apply only
# without a config.
account_dirs=("$HOME/Library/Application Support/Claude-Duo" "$HOME/.claude-duo")
refused=()
if [ -f "$config" ] && [ ! -L "$config" ]; then
    account_dirs=()
    while IFS= read -r line || [ -n "$line" ]; do
        line="$(trim "${line%%#*}")"
        case "$line" in ''|@*) continue ;; esac
        IFS='|' read -r name _bundle desktop _hue env _extra <<<"$line"
        removing "$(trim "$name")" || continue
        desktop="$(expand_home "$(trim "$desktop")")"
        if is_duo_profile desktop "$desktop"; then account_dirs+=("$desktop"); else refused+=("$desktop"); fi
        IFS=';' read -ra pairs <<<"${env:-}"
        for pair in "${pairs[@]-}"; do
            pair="$(trim "$pair")"
            case "$pair" in CLAUDE_CONFIG_DIR=*)
                code="$(expand_home "$(trim "${pair#CLAUDE_CONFIG_DIR=}")")"
                if is_duo_profile code "$code"; then account_dirs+=("$code"); else refused+=("$code"); fi ;;
            esac
        done
    done < "$config"
fi

items=("${DUO_APPS[@]}")
[ -n "$only" ] || items+=("$UPDATER_APP" "${LEGACY_UPDATER_APPS[@]}" "$AGENT" "$DUO_HOME" "$config" "$DUO_LOG_DIR")
# The skills, only the copies the installer put there (never a symlink or the
# user's own): the original's with the whole Squad, and each removed copy's inbox.
skill_dirs=()
[ -n "$only" ] || skill_dirs+=("$HOME/.claude/skills/duo-send" "$HOME/.claude/skills/duo-inbox")
for code in "${account_dirs[@]-}"; do [ -z "$code" ] || skill_dirs+=("$code/skills/duo-inbox"); done
for skill_dir in "${skill_dirs[@]-}"; do
    [ -n "$skill_dir" ] && [ -d "$skill_dir" ] && [ ! -L "$skill_dir" ] && /usr/bin/grep -qxF '<!-- claude-duo: installed by duo/install-duo.sh, which updates it; uninstall-duo.sh removes it -->' "$skill_dir/SKILL.md" 2>/dev/null || continue
    real="$(cd "$skill_dir" && pwd -P)" || continue
    case " ${skill_reals:-} " in *" $real "*) continue ;; esac
    skill_reals="${skill_reals:-} $real"; items+=("$skill_dir")
done
if [ -z "$only" ] && [ "$others" = no ]; then
    items+=("$TOOLSET")
    for txn in "$HOME/Applications"/.claude-txn.*; do items+=("$txn"); done
fi
[ "$account" = no ] || [ "${#account_dirs[@]}" -eq 0 ] || items+=("${account_dirs[@]}")
present=()
for item in "${items[@]}"; do
    [ -e "$item" ] || [ -L "$item" ] && present+=("$item")
done
[ "${#present[@]}" -gt 0 ] || [ -n "$only" ] || { echo "Nothing from $DUO_PRODUCT was found."; exit 0; }

if [ "${#present[@]}" -gt 0 ]; then
    echo 'Moving to the Trash:'
    printf '  %s\n' "${present[@]}"
fi
[ -z "$only" ] || printf 'Removing the Claude %s line from %s (and @links, if it names it).\n' "${DUO_NAMES[0]}" "$config"
[ -n "$only" ] || [ "$others" = no ] || printf 'Keeping %s and %s/.claude-txn.*: another config in %s still uses them.\n' "$TOOLSET" "$HOME/Applications" "$CONFIGS"
if [ "$account" = no ]; then
    [ "${#account_dirs[@]}" -eq 0 ] || { echo 'Keeping the account data (add --include-account-data to move it too):'; printf '  %s\n' "${account_dirs[@]}"; }
elif [ "${#refused[@]}" -gt 0 ]; then
    echo 'Keeping these folders from the copy lines: they are not copy profile locations:'
    printf '  %s\n' "${refused[@]}"
fi
if [ "$ask" = yes ]; then
    [ -t 0 ] || fail 'no terminal to confirm; run again with --yes'
    read -r -p 'Continue? [y/N] ' answer
    case "$answer" in y|Y|yes|Yes) ;; *) echo 'Nothing was changed.'; exit 0 ;; esac
fi

if [ -n "$only" ]; then
    # The line goes first: a config naming a copy whose app is gone would
    # make the next update rebuild it.
    lower="$(duo_lower "${DUO_NAMES[0]}")"
    ( umask 077
      /usr/bin/awk -v name="$lower" '
          { line = $0; sub(/#.*/, "", line); gsub(/^[[:space:]]+|[[:space:]]+$/, "", line) }
          line ~ /^@links claude-dev[[:space:]]/ { target = line; sub(/^@links claude-dev[[:space:]]+/, "", target); if (tolower(target) == name) next }
          line !~ /^@/ && line != "" { first = line; sub(/[[:space:]]*\|.*/, "", first); if (tolower(first) == name) next }
          { print }' "$config" > "$config.only.tmp" ) || { /bin/rm -f "$config.only.tmp"; fail "could not rewrite $config"; }
    /bin/chmod 600 "$config.only.tmp" && /bin/mv -f "$config.only.tmp" "$config" || exit 1
else
    "$LAUNCHCTL" bootout "gui/$(/usr/bin/id -u)/$AGENT_LABEL" >/dev/null 2>&1 || true
fi
[ "${#present[@]}" -gt 0 ] || { printf 'Removed Claude %s from the config.\n' "${DUO_NAMES[0]}"; exit 0; }
# One folder per run, mirroring the paths under the home folder.
label="$DUO_PRODUCT"; [ -z "$only" ] || label="Claude ${DUO_NAMES[0]}"
destination="$HOME/.Trash/$label uninstalled $(/bin/date +%Y-%m-%d\ %H.%M.%S)"
[ ! -e "$destination" ] && [ ! -L "$destination" ] || fail "already exists: $destination"
/bin/mkdir -p -m 700 "$destination" || exit 1
status=0
for item in "${present[@]}"; do
    target="$destination/${item#"$HOME"/}"
    /bin/mkdir -p "${target%/*}" && /bin/mv "$item" "$target" || { duo_error "could not move: $item"; status=1; }
done
# Only an empty folder: rmdir refuses anything else.
[ -n "$only" ] || [ "$others" = yes ] || /bin/rmdir "$CONFIGS" 2>/dev/null || true
printf 'Moved to %s\n' "$destination"
for name in "${DUO_NAMES[@]}"; do
    printf 'The "%s Claude Safe Storage" item stays in the Keychain; delete it in Keychain Access if you want.\n' "$name"
done
exit "$status"
