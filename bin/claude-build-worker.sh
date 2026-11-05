#!/bin/bash -p
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
# Internal worker. The native parent holds a kernel lock for this entire run.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/claude-instances-common.sh"
ci_load_config || exit 1
CI_TX_HELPER="${1:-}"
[ -n "$CI_TX_HELPER" ] && [ -f "$CI_TX_HELPER" ] && [ ! -L "$CI_TX_HELPER" ] && [ -O "$CI_TX_HELPER" ] && [ -x "$CI_TX_HELPER" ] || { ci_error 'worker requires its compiled helper'; exit 1; }
shift
"$CI_TX_HELPER" locked "$CI_APPS" || exit 1
umask 077
build_one() (
    local idx="$1" name="${names[$1]}" dest stage bundle icon old_id build_id icon_changed=0 product_changed=0 asar_hash
    dest="$(ci_app_path "$name")"
    [ ! -L "$dest" ] || { ci_error 'refusing symlinked destination'; return 1; }
    if [ -e "$dest" ]; then
        [ -d "$dest" ] || { ci_error 'destination is not an application directory'; return 1; }
        old_id="$("$CI_PB" -c 'Print :CFBundleIdentifier' "$dest/Contents/Info.plist")" || return 1
        case "$old_id" in "${ids[$idx]}"|com.anthropic.claudefordesktop) ;; *) ci_error 'refusing to overwrite an unrelated app'; return 1 ;; esac
    fi
    ci_require_desktop_idle && ci_require_stopped "$dest" || return 1
    stage="$(mktemp -d "$CI_APPS/.claude-txn.XXXXXXXX")" || return 1
    cleanup() {
        if [ -e "$stage/transaction.txt" ] || [ -L "$stage/transaction.txt" ]; then
            printf 'Transaction retained: %s (inspect with --recover %s).\n' "$stage" "${stage##*/}" >&2
        else
            rm -rf "$stage"
        fi
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    bundle="$stage/instance.app"
    cp -Rc "$CI_SRC" "$bundle" || return 1
    codesign --verify --strict --all-architectures -R "$CI_REQUIREMENT" "$bundle" || return 1
    codesign --verify --deep --strict --all-architectures "$bundle" || return 1
    "$CI_PB" -c "Set :CFBundleIdentifier ${ids[$idx]}" "$bundle/Contents/Info.plist" || return 1
    "$CI_PB" -c "Set :CFBundleDisplayName Claude $name" "$bundle/Contents/Info.plist" || return 1
    "$CI_PB" -c 'Set :CFBundleExecutable launcher' "$bundle/Contents/Info.plist" || return 1
    local key links_changed=0
    for key in CFBundleURLTypes NSUserActivityTypes; do
        if "$CI_PB" -c "Print :$key" "$bundle/Contents/Info.plist" >/dev/null 2>&1; then
            "$CI_PB" -c "Delete :$key" "$bundle/Contents/Info.plist" || return 1
        fi
    done
    if ci_links_for "$idx"; then
        # The copy's own way in for links: claude:// stays with the official
        # app; the Desktop also accepts claude-dev://. The record expects this.
        "$CI_PB" -c 'Add :CFBundleURLTypes array' -c 'Add :CFBundleURLTypes:0 dict' \
            -c "Add :CFBundleURLTypes:0:CFBundleURLName string Claude $name links" \
            -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes array' \
            -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string claude-dev' "$bundle/Contents/Info.plist" || return 1
        links_changed=1
    fi
    icon="$CI_ICONS/$(ci_lower "$name").icns"
    [ ! -L "$icon" ] && { [ ! -e "$icon" ] || [ -f "$icon" ]; } || { ci_error 'icon master is not a regular file'; return 1; }
    if [ ! -f "$icon" ]; then
        if ! "$(dirname "${BASH_SOURCE[0]}")/claude-recolor-icon.sh" "${hues[$idx]}" "$icon"; then
            printf 'warning: icon generation failed; using stock icon\n' >&2
        fi
    fi
    if [ -f "$icon" ]; then
        [ ! -L "$icon" ] && [ -O "$icon" ] && ci_no_allow_acl "$icon" || { ci_error 'unsafe icon master'; return 1; }
        cp "$icon" "$bundle/Contents/Resources/appicon.icns" || return 1
        "$CI_PB" -c 'Set :CFBundleIconFile appicon' "$bundle/Contents/Info.plist" || return 1
        if "$CI_PB" -c 'Print :CFBundleIconName' "$bundle/Contents/Info.plist" >/dev/null 2>&1; then
            "$CI_PB" -c 'Delete :CFBundleIconName' "$bundle/Contents/Info.plist" || return 1
        fi
        icon_changed=1
    fi
    build_id="$(/usr/bin/uuidgen | LC_ALL=C tr '[:upper:]' '[:lower:]')" || return 1
    printf '%s\n' "$build_id" > "$bundle/Contents/Resources/mcd-build-id" || return 1
    chmod 644 "$bundle/Contents/Resources/mcd-build-id" || return 1
    if [ "$CI_MODE" = original-copy ]; then
        # Own Electron app.name, hence its own "<productName> Safe Storage"
        # Keychain item; the record below regenerates this from the source.
        ci_integrity_helper_valid || return 1
        asar_hash="$("$CI_INTEGRITY_HELPER" product-name "$bundle" "$name")" || return 1
        [[ "$asar_hash" =~ ^[0-9a-f]{64}$ ]] || { ci_error 'unexpected archive header digest'; return 1; }
        "$CI_PB" -c "Set :ElectronAsarIntegrity:Resources/app.asar:hash $asar_hash" "$bundle/Contents/Info.plist" || return 1
        product_changed=1
    fi
    ci_render_launcher "$idx" > "$bundle/Contents/MacOS/launcher" || return 1
    chmod 755 "$bundle/Contents/MacOS/launcher" || return 1
    plutil -lint "$bundle/Contents/Info.plist" >/dev/null || return 1
    # Sign only the executable and outer bundle; never --deep.
    codesign --force -s - "$bundle/Contents/MacOS/Claude" || return 1
    codesign --force -s - "$bundle" || return 1
    ci_integrity_helper_valid || return 1
    ci_integrity_settled record "$CI_ICONS/integrity" "$build_id" "$bundle" "$CI_SRC" "$name" "${ids[$idx]}" "$(ci_integrity_binding "$idx")" "$icon_changed" "$product_changed" "$links_changed" || return 1
    ci_verify_one "$bundle" "$idx" || return 1
    ci_prepare_data_dir "${dirs[$idx]}" desktop || return 1
    ci_prepare_data_dir "${code_dirs[$idx]}" code || return 1
    ci_require_desktop_idle && ci_require_stopped "$dest" || return 1
    "$CI_TX_HELPER" publish "$CI_APPS" "${stage##*/}" "$name" || return 1
    ci_verify_one "$dest" "$idx" || { ci_error 'published app failed checks; transaction and previous bundle retained'; return 1; }
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$dest" >/dev/null 2>&1 || printf 'warning: LaunchServices registration failed\n' >&2
    printf 'Built Claude %s; local signature, independent GUI profile.\n' "$name"
)

if [ "${1:-}" = --recover ]; then
    [ "$#" -eq 2 ] || { ci_error 'usage: --recover .claude-txn.XXXXXXXX'; exit 2; }
    observation="$("$CI_TX_HELPER" inspect "$CI_APPS" "$2")" || exit 1
    read -r state instance transaction <<<"$observation"
    idx=""
    for i in "${!names[@]}"; do [ "${names[$i]}" != "$instance" ] || idx="$i"; done
    [ -n "$idx" ] || { ci_error 'transaction instance is not in the current config; nothing changed'; exit 1; }
    if [ "$state" = COMMITTED ]; then
        ci_verify_one "$(ci_app_path "$instance")" "$idx" || { ci_error 'bundle checks failed; transaction retained, nothing changed'; exit 1; }
        printf 'COMMITTED: Claude %s is at the published path; any previous bundle remains in %s/%s/instance.app.\n' "$instance" "$CI_APPS" "$transaction"
    elif [ "$state" = PREPARED ]; then
        printf 'PREPARED layout: the candidate remains in %s/%s/instance.app; the destination still has its recorded previous inode (or is absent for a first install).\n' "$CI_APPS" "$transaction"
    else
        ci_error 'unknown transaction state; nothing changed'; exit 1
    fi
    printf 'No bundle/profile was restored, launched or deleted. An older bundle alone is not a safe profile downgrade.\n'
    exit 0
fi
case "${1:-all}" in
    all) [ "$#" -le 1 ] || exit 2; selected=("${!names[@]}") ;;
    *) [ "$#" -eq 1 ] || exit 2
       selected=(); for i in "${!names[@]}"; do
           if [ "$(ci_lower "$1")" = "$(ci_lower "${names[$i]}")" ]; then selected+=("$i"); fi
       done
       [ "${#selected[@]}" -gt 0 ] || { ci_error 'unknown instance or option'; exit 2; } ;;
esac
[ -d "$CI_SRC" ] && [ ! -L "$CI_SRC" ] || { ci_error 'missing or symlinked source Claude.app'; exit 1; }
codesign --verify --strict --all-architectures -R "$CI_REQUIREMENT" "$CI_SRC" || { ci_error 'source is not a valid Anthropic-signed Claude.app'; exit 1; }
codesign --verify --deep --strict --all-architectures "$CI_SRC" || { ci_error 'source nested code/resource validation failed'; exit 1; }
spctl --assess --type execute "$CI_SRC" || { ci_error 'Gatekeeper did not accept the source'; exit 1; }
rc=0
for i in "${selected[@]}"; do build_one "$i" || rc=1; done
ci_status
exit "$rc"
