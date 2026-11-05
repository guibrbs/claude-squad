#!/bin/bash -p
# One-command Claude Squad setup from a reviewed checkout of this repository:
# checks prerequisites, installs the pinned toolset, writes the Duo config,
# installs the "Update Claude Squad" app and the update check, then builds
# the Duo (asking before closing Claude). Run from inside Claude, it hands the
# build to the "Update Claude Squad" app instead. Safe to run again.
#   --no-auto-check   do not install the launch agent that asks after updates
#   --no-build        prepare everything but do not build the Duo now
#   --links           let the Duo take claude-dev:// links (duo-send.py open):
#                     adds `@links claude-dev` to the config before building
#   --no-skill        do not install the duo-send and duo-inbox skills
#   --add NAME        add another managed copy, "Claude NAME", for one more
#                     account: a line with its own bundle id, Desktop profile
#                     (~/Library/Application Support/Claude-NAME), Code folder
#                     (~/.claude-name) and icon color, then the build covers it
DUO_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
source "$DUO_TOOLS/duo-common.sh" || exit 1
set -u
REPO="$(cd "$DUO_TOOLS/.." && pwd -P)"
LAUNCHCTL=/bin/launchctl
OSACOMPILE=/usr/bin/osacompile
XCODE_SELECT=/usr/bin/xcode-select
AGENT_LABEL=io.github.guibrbs.claude-duo.update-check
AGENT="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
UPDATER_APP="$HOME/Applications/Update Claude Squad.app"
# Names earlier releases gave the updater.
LEGACY_UPDATER_APPS=("$HOME/Applications/Update Claude Duo.app" "$HOME/Applications/Atualizar Claude Duo.app")

auto_check=yes; build=yes; links=no; skill=yes; add=""
step() { printf '\n==> %s\n' "$*"; }
fail() { duo_error "$*"; exit 1; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-auto-check) auto_check=no ;;
        --no-build) build=no ;;
        --links) links=yes ;;
        --no-skill) skill=no ;;
        --add) [ "$#" -ge 2 ] || { duo_error '--add needs a name'; exit 2; }; add="$2"; shift ;;
        *) duo_error "unknown option: $1"; exit 2 ;;
    esac
    shift
done
if [ -n "$add" ]; then
    # Same rule as the toolset's instance names; "original" names the official
    # account in loop-duo and duo-send.
    [[ "$add" =~ ^[A-Za-z][A-Za-z0-9_-]{0,63}$ ]] || fail "invalid name for --add: use a letter followed by letters, digits, - or _"
    [ "$(duo_lower "$add")" != original ] || fail '"original" is the official Claude; choose another name'
fi

step "Checking prerequisites"
[ "$(/usr/bin/uname -s)" = Darwin ] || fail "$DUO_PRODUCT only works on macOS"
[ "$(/usr/bin/id -u)" != 0 ] || fail 'run as your own user, not as root'
"$XCODE_SELECT" -p >/dev/null 2>&1 || fail 'Xcode Command Line Tools are missing: run  xcode-select --install  and try again'
[ -d "$DUO_SOURCE_APP" ] || fail "install the official Claude in $DUO_SOURCE_APP first"
/usr/bin/python3 -I -c 'pass' 2>/dev/null || fail 'python3 is unavailable (it comes with the Command Line Tools)'
if [ ! -e "$HOME/Applications" ]; then
    /bin/mkdir -m 700 "$HOME/Applications" || exit 1
fi
[ -d "$HOME/Applications" ] && [ ! -L "$HOME/Applications" ] && [ -O "$HOME/Applications" ] || fail '~/Applications must be a folder you own'
[ "$(/usr/bin/stat -f '%Lp' "$HOME/Applications")" = 700 ] || fail '~/Applications must have mode 700: run  chmod 700 ~/Applications  and try again'

step "Duo configuration"
/bin/mkdir -p "$DUO_HOME" "$HOME/.config/claude-instances" || exit 1
/bin/chmod 700 "$DUO_HOME" "$HOME/.config/claude-instances" || exit 1
config=""
if [ -f "$DUO_CONFIG_POINTER" ]; then
    config="$(duo_config)" || exit 1
elif [ -f "$HOME/.config/claude-instances/claude-duo.conf" ]; then
    config="$HOME/.config/claude-instances/claude-duo.conf"
elif [ -f "$HOME/.config/claude-instances/duo-validation.conf" ] && /usr/bin/grep -qx '@mode original-copy' "$HOME/.config/claude-instances/duo-validation.conf"; then
    config="$HOME/.config/claude-instances/duo-validation.conf"  # Earlier manual setup.
else
    config="$HOME/.config/claude-instances/claude-duo.conf"
    owner="$(printf '%s' "$USER" | /usr/bin/tr -c 'A-Za-z0-9-' '-' | /usr/bin/tr 'A-Z' 'a-z')"
    ( set -o noclobber
      printf '@mode original-copy\n@apps user\nDuo | io.github.%s.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo\n' "${owner:-user}" > "$config" ) || fail "could not create $config"
    /bin/chmod 600 "$config" || exit 1
fi
printf '%s\n' "$config" > "$DUO_CONFIG_POINTER" && /bin/chmod 600 "$DUO_CONFIG_POINTER" || exit 1
duo_load_copies "$config" || exit 1
if [ -n "$add" ]; then
    known=no
    for name in "${DUO_NAMES[@]}"; do [ "$(duo_lower "$name")" != "$(duo_lower "$add")" ] || known=yes; done
    if [ "$known" = yes ]; then
        echo "Claude $add is already in the config."
    else
        lower="$(duo_lower "$add")"
        owner="$(printf '%s' "$USER" | /usr/bin/tr -c 'A-Za-z0-9-' '-' | /usr/bin/tr 'A-Z' 'a-z')"
        # The first icon color no other copy uses (blue is the Duo's).
        used="$(/usr/bin/awk -F'|' '!/^[[:space:]]*(@|#|$)/ { gsub(/[[:space:]]/, "", $4); print $4 }' "$config")"
        hue=212
        for candidate in 212 275 142 30 320 175 55 0; do
            printf '%s\n' "$used" | /usr/bin/grep -qx "$candidate" || { hue="$candidate"; break; }
        done
        for folder in "$HOME/Library/Application Support/Claude-$add" "$HOME/.claude-$lower"; do
            [ ! -e "$folder" ] || echo "Reusing $folder: it already exists (an earlier Claude $add?)."
        done
        printf '%s | io.github.%s.claude-%s | ~/Library/Application Support/Claude-%s | %s | CLAUDE_CONFIG_DIR=~/.claude-%s\n' \
            "$add" "${owner:-user}" "$lower" "$add" "$hue" "$lower" >> "$config" || fail "could not add Claude $add to $config"
        echo "Added: Claude $add"
        duo_load_copies "$config" || exit 1
    fi
fi
# LaunchServices gives claude-dev:// to one app: with several copies the
# @links line names it. A bare line meant the only copy, which is the first.
links_line=""
if [ "${#DUO_NAMES[@]}" -gt 1 ] && /usr/bin/grep -qx '[[:space:]]*@links claude-dev[[:space:]]*' "$config"; then
    links_line="@links claude-dev ${DUO_NAMES[0]}"
elif [ "$links" = yes ] && ! /usr/bin/grep -Eq '^[[:space:]]*@links claude-dev([[:space:]]|$)' "$config"; then
    links_line="@links claude-dev"
    [ "${#DUO_NAMES[@]}" -eq 1 ] || links_line="@links claude-dev ${DUO_NAMES[0]}"
fi
if [ -n "$links_line" ]; then
    # One @links line right after `@apps`; any other @links line goes.
    ( umask 077
      /usr/bin/awk -v links="$links_line" '/^[[:space:]]*@links/ { next } { print } /^[[:space:]]*@apps/ && !done { print links; done = 1 } END { exit done ? 0 : 3 }' \
          "$config" > "$config.links.tmp" ) || { /bin/rm -f "$config.links.tmp"; fail "no @apps line in $config: add  $links_line  before the copy lines by hand"; }
    /bin/chmod 600 "$config.links.tmp" && /bin/mv -f "$config.links.tmp" "$config" || exit 1
    echo "Links: claude-dev:// goes to Claude ${links_line#@links claude-dev } (the build below applies it)"
fi
echo "Config: $config"

step "Installing the versioned toolset"
work="$(/usr/bin/mktemp -d "$DUO_HOME/.install.XXXXXXXX")" || exit 1
trap '/bin/rm -rf "$work"' EXIT
digest="$(/usr/bin/python3 -I "$REPO/scripts/prepare-toolset.py" "$work/package")" || fail 'could not prepare the toolset package (local changes in bin/?)'
/bin/mkdir -m 700 "$work/tmp" || exit 1
/usr/bin/env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" PATH="$PATH" LANG=en_US.UTF-8 TMPDIR="$work/tmp" \
    /bin/bash -p "$REPO/bin/claude-toolset.sh" install "$work/package" "$digest" || fail 'toolset installation failed'
"$DUO_DISPATCHER" verify >/dev/null || fail 'the installed toolset failed verification'

step "Installing the updater"
for file in duo-common.sh update-duo.sh check-update.sh; do
    /usr/bin/install -m 700 "$DUO_TOOLS/$file" "$DUO_HOME/$file" || exit 1
done
/usr/bin/install -m 700 "$REPO/scripts/share-code-profile.py" "$DUO_HOME/share-code-profile.py" || exit 1
# duo-send imports loop-duo from its own folder; the skill calls this copy.
for file in duo-send.py loop-duo.py; do
    /usr/bin/install -m 700 "$DUO_TOOLS/$file" "$DUO_HOME/$file" || exit 1
done
# Earlier releases named the updater after the Duo (and in Portuguese);
# remove them so only one updater exists.
/bin/rm -rf "${LEGACY_UPDATER_APPS[@]}"
/bin/rm -f "$DUO_HOME/atualizar-duo.sh" "$DUO_HOME/verificar-atualizacao.sh"
script="do shell script \"/bin/bash -p \" & quoted form of \"$(duo_as_text "$DUO_HOME/update-duo.sh")\" & \" >/dev/null 2>&1 &\""
/bin/rm -rf "$UPDATER_APP"
"$OSACOMPILE" -o "$UPDATER_APP" -e "$script" || fail 'could not create the Update Claude Squad app'
echo "App: $UPDATER_APP"

skill_mark='<!-- claude-duo: installed by duo/install-duo.sh, which updates it; uninstall-duo.sh removes it -->'
# Installs one of this checkout's skills into a Code folder's skills, as a marked
# copy: reruns refresh it; a skill of the user's own, or a symlink, is kept.
install_skill() {
    local name="$1" code="$2" purpose="$3" dir
    dir="$code/skills/$name"
    if [ ! -d "$code" ] || [ -L "$code" ]; then
        echo "Skipped $name: $code does not exist yet; run this again after opening Code there once."
    elif [ -L "$dir" ] || { [ -e "$dir" ] && ! /usr/bin/grep -qxF "$skill_mark" "$dir/SKILL.md" 2>/dev/null; }; then
        echo "Kept $dir: it was not installed by $DUO_PRODUCT."
    else
        /bin/mkdir -p "$dir" || exit 1
        /usr/bin/install -m 644 "$DUO_TOOLS/skills/$name/SKILL.md" "$dir/SKILL.md" || exit 1
        echo "Skill: $dir ($purpose)"
    fi
}
if [ "$skill" = yes ]; then
    step "Skills: duo-send for the original Claude, duo-inbox for each copy"
    install_skill duo-send "$HOME/.claude" "ask the original Claude to send work to the Duo"
    for i in "${!DUO_NAMES[@]}"; do
        [ -z "${DUO_CODE_DIRS[$i]}" ] || install_skill duo-inbox "${DUO_CODE_DIRS[$i]}" \
            "send /duo-inbox in a Claude ${DUO_NAMES[$i]} session to make it the inbox"
    done
fi

if [ "$auto_check" = yes ]; then
    step "Update prompt when Claude updates"
    /bin/mkdir -p "$HOME/Library/LaunchAgents" || exit 1
    new="$work/agent.plist"
    "$PLISTBUDDY" -c "Add :Label string $AGENT_LABEL" \
        -c 'Add :ProgramArguments array' -c 'Add :ProgramArguments:0 string /bin/bash' \
        -c 'Add :ProgramArguments:1 string -p' -c "Add :ProgramArguments:2 string $DUO_HOME/check-update.sh" \
        -c 'Add :StartInterval integer 3600' -c 'Add :RunAtLoad bool true' \
        -c 'Add :ProcessType string Background' "$new" >/dev/null || exit 1
    /usr/bin/plutil -lint "$new" >/dev/null || exit 1
    "$LAUNCHCTL" bootout "gui/$(/usr/bin/id -u)/$AGENT_LABEL" >/dev/null 2>&1 || true
    /bin/mv "$new" "$AGENT" && /bin/chmod 644 "$AGENT" || exit 1
    "$LAUNCHCTL" bootstrap "gui/$(/usr/bin/id -u)" "$AGENT" || fail 'could not enable the update prompt'
    echo "Agent: $AGENT (checks every hour)"
fi

deferred=no
if [ "$build" = yes ]; then
    step "Building $(duo_label)"
    current=yes; inside=no
    for app in "${DUO_APPS[@]}"; do
        [ -d "$app" ] && [ "$(duo_version "$app")" = "$(duo_version "$DUO_SOURCE_APP")" ] || current=no
    done
    for app in "$DUO_SOURCE_APP" "${DUO_APPS[@]}"; do duo_inside_app "$app" && inside=yes; done
    if [ "$current" = yes ] && duo_refresh --verify >/dev/null 2>&1; then
        echo "$(duo_label) already up to date and verified."
    elif [ "$inside" = yes ]; then
        # Building quits the Claude this installer runs in; the updater app
        # is launched by LaunchServices and survives that.
        "$OPEN" -a "$UPDATER_APP" || fail 'could not open Update Claude Squad; open it from the Finder'
        deferred=yes
        printf 'This installer runs inside Claude (in the Code tab, for example), and building quits Claude.\nOpened "Update Claude Squad", which runs outside it: choose "%s" and wait for the notification that it finished.\n' "$(duo_t "Update now" "Atualizar agora")"
    else
        /bin/bash -p "$DUO_HOME/update-duo.sh" || fail "the build failed; see the log in $DUO_LOG_DIR"
    fi
fi
target="~/Applications/Claude Duo and sign in with your second account."
[ -z "$add" ] || target="~/Applications/Claude $add and sign in with the account for it."
if [ "$deferred" = yes ]; then
    printf '\nAlmost done. After the notification, open %s\n' "$target"
else
    printf '\nDone. Open %s\n' "$target"
fi
if [ -n "$add" ]; then
    printf 'For loop-duo and duo-send, also sign in its command line, in a Terminal outside Claude:\n  CLAUDE_CONFIG_DIR=~/.claude-%s claude auth login\n' "$(duo_lower "$add")"
fi
printf 'To update later: open "Update Claude Squad" (or accept the prompt when Claude updates).\n'
