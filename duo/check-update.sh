#!/bin/bash -p
# Run by the launch agent: when the official Claude has a different version
# from a managed copy, asks (at most once a day per version) whether to update now.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/duo-common.sh" || exit 1
set -u

duo_load_copies 2>/dev/null || exit 0
source_version="$(duo_version "$DUO_SOURCE_APP")" || exit 0
[ -n "$source_version" ] || exit 0
# Copies that are not built yet are the installer's job, not this prompt's.
behind=""; behind_pt=""
for i in "${!DUO_NAMES[@]}"; do
    version="$(duo_version "${DUO_APPS[$i]}")" || continue
    [ "$version" != "$source_version" ] || continue
    behind="$behind${behind:+, }Claude ${DUO_NAMES[$i]} is on $version"
    behind_pt="$behind_pt${behind_pt:+, }o Claude ${DUO_NAMES[$i]} está na $version"
done
[ -n "$behind" ] || exit 0

state="$DUO_HOME/asked-version"
today="$(/bin/date +%Y-%m-%d)"
previous=""
[ ! -f "$state" ] || [ -L "$state" ] || IFS= read -r previous < "$state" || true
[ "$previous" != "$source_version $today" ] || exit 0
printf '%s %s\n' "$source_version" "$today" > "$state"

update_now="$(duo_t "Update now" "Atualizar agora")"
answer="$(duo_ask "$(duo_t "Claude was updated to $source_version and $behind. Updating quits and reopens Claude and $(duo_label)." "O Claude foi atualizado para $source_version e $behind_pt. Para atualizar, o Claude e $(duo_label --pt-article) serão fechados e reabertos.")" "$update_now" "$(duo_t "Later" "Depois")")"
[ "$answer" = "$update_now" ] || exit 0
exec /bin/bash -p "$DUO_HOME/update-duo.sh" --yes
