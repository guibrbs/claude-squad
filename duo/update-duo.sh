#!/bin/bash -p
# Claude Squad's updater: rebuilds every managed copy (Claude Duo, and any
# other in the config) from
# the current official Claude: asks first, quits the original and the copies,
# rebuilds and verifies with the installed toolset, relinks shared Code files,
# then reopens whatever was open. --yes skips the question.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/duo-common.sh" || exit 1
set -u

ask=yes
case "${1:-}" in --yes) ask=no ;; '') ;; *) duo_error 'usage: update-duo.sh [--yes]'; exit 2 ;; esac
/bin/mkdir -p "$DUO_LOG_DIR" || exit 1
LOG="$DUO_LOG_DIR/update-$(/bin/date +%Y%m%d-%H%M%S).log"
log() { printf '%s\n' "$*" >> "$LOG"; }

if ! config="$(duo_config 2>>"$LOG")" || ! duo_load_copies "$config" 2>>"$LOG"; then
    duo_notify "$DUO_PRODUCT" "$(duo_t "$DUO_PRODUCT is not installed. Run duo/install-duo.sh." "O $DUO_PRODUCT não está instalado. Rode o duo/install-duo.sh.")"
    exit 1
fi
copies="$(duo_label)"; copies_pt="$(duo_label --pt-article)"
plural() { [ "${#DUO_NAMES[@]}" -eq 1 ] && printf '%s' "$1" || printf '%s' "$2"; }
if [ "$ask" = yes ]; then
    update_now="$(duo_t "Update now" "Atualizar agora")"
    answer="$(duo_ask "$(duo_t "To update $copies, Claude and $copies will quit and reopen at the end. Save your work first." "Para atualizar $copies_pt, o Claude e $copies_pt serão fechados e reabertos no fim. Salve o que estiver fazendo.")" "$update_now" "$(duo_t "Cancel" "Cancelar")")"
    [ "$answer" = "$update_now" ] || { log "cancelled"; exit 0; }
fi

was_open=()
for app in "$DUO_SOURCE_APP" "${DUO_APPS[@]}"; do
    duo_app_running "$app" && was_open+=("$app")
done
reopen() {
    local app
    for app in "${was_open[@]-}"; do
        [ -n "$app" ] && [ -d "$app" ] && { "$OPEN" -a "$app" >/dev/null 2>&1 || true; }
    done
}

if ! duo_quit_all; then
    log "apps did not quit within ${DUO_QUIT_TIMEOUT}s; still running (PID executable):"
    { duo_app_processes "$DUO_SOURCE_APP"; for app in "${DUO_APPS[@]}"; do duo_app_processes "$app" any; done; } | while IFS= read -r line; do log "  $line"; done
    duo_notify "$DUO_PRODUCT" "$(duo_t "Could not quit Claude and $copies. Quit them and try again. Log: $LOG" "Não consegui fechar o Claude e $copies_pt. Feche todos e tente de novo. Log: $LOG")"
    exit 1
fi
duo_notify "$DUO_PRODUCT" "$(duo_t "Updating $copies… this takes about a minute." "Atualizando $copies_pt… leva cerca de um minuto.")"
versions=""; before=()
for i in "${!DUO_NAMES[@]}"; do
    before+=("$(duo_version "${DUO_APPS[$i]}" || echo none)")
    versions="$versions $(duo_lower "${DUO_NAMES[$i]}") ${before[$i]}"
done
source_version="$(duo_version "$DUO_SOURCE_APP")"
log "== $(/bin/date) source $source_version$versions"

status=0
{ duo_refresh && duo_refresh --verify; } >> "$LOG" 2>&1 || status=$?
if [ "$status" -eq 0 ] && [ -f "$DUO_HOME/share-code-profile.py" ]; then
    for i in "${!DUO_NAMES[@]}"; do
        code="${DUO_CODE_DIRS[$i]}"
        [ -n "$code" ] && [ -f "$code/.shared-from-original.json" ] || continue
        # Links memories of projects created since the last run; never touches login data.
        /usr/bin/python3 -I "$DUO_HOME/share-code-profile.py" --apply --destination "$code" --app "${DUO_APPS[$i]}" >> "$LOG" 2>&1 \
            || log "warning: shared Code files of Claude ${DUO_NAMES[$i]} were not refreshed"
    done
fi

reopen
if [ "$status" -eq 0 ]; then
    log "OK"
    version="$(duo_version "${DUO_APPS[0]}")"
    duo_notify "$DUO_PRODUCT" "$(duo_t "$copies updated to $version and verified." "$(plural "$copies atualizado para $version e verificado." "$copies atualizados para $version e verificados.")")"
else
    log "FAILED ($status)"
    # The native idle check names processes that run a deleted executable,
    # typically a helper of the previous Claude kept alive by Chrome.
    stale=""
    while IFS= read -r line; do
        case "$line" in "process "*") runs a deleted executable"*) stale="${line#process }"; break ;; esac
    done < "$LOG"
    if [ -n "$stale" ]; then
        name="${stale#* (}"; name="${name%%) runs*}"; pid="${stale%% *}"
        duo_notify "$DUO_PRODUCT" "$(duo_t "Update stopped: $name (PID $pid) still runs a file from the previous Claude. Quit that process and try again. Log: $LOG" "A atualização parou: $name (PID $pid) ainda roda um arquivo da versão anterior do Claude. Encerre esse processo e tente de novo. Log: $LOG")"
    else
        # The build publishes one copy at a time: a failure after the first
        # leaves the copies already rebuilt on the new version.
        updated=(); behind=()
        for i in "${!DUO_NAMES[@]}"; do
            now="$(duo_version "${DUO_APPS[$i]}" || echo none)"
            # Updated means rebuilt by this run onto the source's version;
            # a copy that changed to anything else is still behind.
            if [ "$now" = "$source_version" ]; then
                [ "$now" = "${before[$i]}" ] || updated+=("${DUO_NAMES[$i]}")
            else
                behind+=("${DUO_NAMES[$i]}")
            fi
        done
        # Portuguese agreement for a list of copies: COUNT SINGULAR PLURAL.
        agree() { [ "$1" -eq 1 ] && printf '%s' "$2" || printf '%s' "$3"; }
        if [ "${#updated[@]}" -eq 0 ]; then
            duo_notify "$DUO_PRODUCT" "$(duo_t "The update failed; the previous copies were kept. Log: $LOG" "A atualização falhou; as cópias anteriores foram mantidas. Log: $LOG")"
        elif [ "${#behind[@]}" -eq 0 ]; then
            duo_notify "$DUO_PRODUCT" "$(duo_t "$(duo_label "${updated[@]}") updated to $source_version, but the update stopped before its last check. Run it again to finish. Log: $LOG" "$(duo_label --pt-article "${updated[@]}") $(agree "${#updated[@]}" "foi atualizado" "foram atualizados") para $source_version, mas a atualização parou antes da última conferência. Rode de novo para concluir. Log: $LOG")"
        else
            duo_notify "$DUO_PRODUCT" "$(duo_t "$(duo_label "${updated[@]}") updated to $source_version; $(duo_label "${behind[@]}") kept the previous version. Run the update again. Log: $LOG" "$(duo_label --pt-article "${updated[@]}") $(agree "${#updated[@]}" "foi atualizado" "foram atualizados") para $source_version; $(duo_label --pt-article "${behind[@]}") $(agree "${#behind[@]}" "ficou" "ficaram") na versão anterior. Rode a atualização de novo. Log: $LOG")"
        fi
    fi
fi
exit "$status"
