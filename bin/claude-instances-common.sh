#!/bin/bash
# Shared data-only configuration and verification; macOS Bash 3.2 compatible.
CI_SRC="/Applications/Claude.app"
CI_SYSTEM_APPS="/Applications"
CI_APPS="$CI_SYSTEM_APPS"
CI_APP_SCOPE=system
CI_CONF="${CLAUDE_INSTANCES_CONF:-$HOME/.config/claude-instances/instances.conf}"
CI_ICONS="$HOME/.local/share/claude-instances"
CI_PB=/usr/libexec/PlistBuddy
CI_REQUIREMENT='=anchor apple generic and certificate leaf[subject.OU] = "Q6L2SF6YDW" and identifier "com.anthropic.claudefordesktop"'
ci_error() { printf 'error: %s\n' "$*" >&2; }
ci_trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}
ci_lower() { printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]'; }
ci_expand() {
    case "$1" in '~') printf '%s\n' "$HOME" ;; '~/'*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;; *) printf '%s\n' "$1" ;; esac
}
# Resolve existing ancestors physically; never interpret config as shell code.
ci_canonical_dir() (
    local path suffix="" part physical
    path="$(ci_expand "$1")" || exit 1
    case "$path" in /*) ;; *) ci_error 'directory must be absolute'; exit 1 ;; esac
    case "$path" in *$'\n'*|*$'\r'*|*$'\t'*) ci_error 'control character in directory'; exit 1 ;; esac
    while [ "$path" != / ] && [ "${path%/}" != "$path" ]; do path="${path%/}"; done
    case "$path/" in *'/../'*|*'/./'*|*'//'*) ci_error 'dot components or repeated slashes in directory'; exit 1 ;; esac
    while [ ! -d "$path" ]; do
        if [ -e "$path" ] || [ -L "$path" ]; then ci_error 'directory is a file or dangling link'; exit 1; fi
        part="${path##*/}"; suffix="/$part$suffix"; path="${path%/*}"
        [ -n "$path" ] || path=/
    done
    physical="$(cd -- "$path" && pwd -P)" || exit 1
    printf '%s%s\n' "${physical%/}" "$suffix"
)
ci_no_allow_acl() {
    local snapshot line first=yes
    snapshot="$(LC_ALL=C /bin/ls -lde "$1")" || return 1
    while IFS= read -r line; do
        if [ "$first" = yes ]; then first=no; continue; fi
        # Fail closed on custom allow ACLs, including inherited grants. Standard
        # macOS deny-delete ACLs are fine. Do not attempt to interpret principals.
        if [[ "$line" =~ [[:space:]]allow[[:space:]] ]]; then ci_error 'custom allow ACL; review permissions first'; return 1; fi
        if [[ "$line" =~ ^[[:space:]]*[0-9]+:.*[[:space:]]deny[[:space:]] ]]; then continue; fi
        ci_error 'custom allow ACL or unknown permission metadata; review permissions first'; return 1
    done <<<"$snapshot"
}
ci_owned_directory_chain() {
    local path="$1" ci_home_path="$2" mode
    while [ ! -d "$path" ]; do path="${path%/*}"; done
    while :; do
        [ -d "$path" ] && [ ! -L "$path" ] && [ -O "$path" ] || { ci_error 'data directory ancestor must be owned by this user and not a symlink'; return 1; }
        mode="$(/usr/bin/stat -f '%Lp' "$path")" || return 1
        [[ "$mode" =~ ^[0-7]{3,4}$ ]] && [ "$((8#$mode & 0022))" -eq 0 ] || { ci_error 'data directory ancestor is writable by another user'; return 1; }
        ci_no_allow_acl "$path" || return 1
        [ "$path" != "$ci_home_path" ] || return 0
        path="${path%/*}"
        case "$path/" in "$ci_home_path/"*) ;; *) ci_error 'directory escaped home'; return 1 ;; esac
    done
}
ci_safe_data_dir() {
    local path="$1" kind="$2" ci_home_path leaf relative base LC_ALL=C
    ci_home_path="$(ci_canonical_dir "$HOME")" || return 1
    case "$path" in *.app|*.app/*) ci_error 'application bundles cannot be data directories'; return 1 ;; esac
    # Only managed locations and the existing flat Claude profile layout.
    # An arbitrary home/system directory must never be chmod'ed or cleaned.
    base="$ci_home_path/Library/Application Support/Multi Claude Desktop/Profiles"
    case "$path" in "$base/"*)
        relative="${path#"$base/"}"; leaf="${relative%%/*}"
        [[ "$leaf" =~ ^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$ ]] && [ "$relative" = "$leaf/$kind" ] || { ci_error 'invalid managed profile location'; return 1; } ;;
    *)
        case "$kind:$path" in
            "desktop:$ci_home_path/Library/Application Support/Claude"|"code:$ci_home_path/.claude") ;;
            "desktop:$ci_home_path/Library/Application Support/Claude-"*)
                leaf="${path#"$ci_home_path/Library/Application Support/Claude-"}"
                [ -n "$leaf" ] && [[ "$leaf" != */* ]] || { ci_error 'legacy desktop profile must be a flat Claude-* directory'; return 1; } ;;
            "code:$ci_home_path/.claude-"*)
                leaf="${path#"$ci_home_path/.claude-"}"
                [ -n "$leaf" ] && [[ "$leaf" != */* ]] || { ci_error 'legacy Code profile must be a flat .claude-* directory'; return 1; } ;;
            *) ci_error 'data directory must use a managed location or the existing Claude profile layout inside home'; return 1 ;;
        esac ;;
    esac
    [[ "${leaf:-}" != *[!\ -~]* ]] || { ci_error 'legacy profile suffix must be printable ASCII; use managed UUID locations for new profiles'; return 1; }
    ci_owned_directory_chain "$path" "$ci_home_path"
}
ci_paths_overlap() {
    local left right
    left="$(ci_lower "$1")"; right="$(ci_lower "$2")"
    case "$left/" in "$right/"*) return 0 ;; esac
    case "$right/" in "$left/"*) return 0 ;; esac
    return 1
}
ci_prepare_data_dir() {
    local path="$1" kind="$2" current
    current="$(ci_canonical_dir "$path")" && [ "$current" = "$path" ] && ci_safe_data_dir "$path" "$kind" || { ci_error 'data directory changed; refusing mutation'; return 1; }
    ci_prepare_private_directory "$path"
}
ci_prepare_private_directory() {
    local path="$1" current ci_home_path
    ci_home_path="$(ci_canonical_dir "$HOME")" || return 1
    current="$(ci_canonical_dir "$path")" && [ "$current" = "$path" ] || { ci_error 'private directory changed or contains a symlink'; return 1; }
    case "$path/" in "$ci_home_path/"*) [ "$path" != "$ci_home_path" ] || return 1 ;; *) return 1 ;; esac
    ci_owned_directory_chain "$path" "$ci_home_path" || return 1
    /bin/mkdir -p "$path" || return 1
    current="$(ci_canonical_dir "$path")" && [ "$current" = "$path" ] && ci_owned_directory_chain "$path" "$ci_home_path" || return 1
    /bin/chmod 700 "$path"
}
ci_user_app_directory() {
    local ci_home_path="$1" expected physical mode
    expected="$ci_home_path/Applications"
    # This is a fixed, pre-existing private directory, never an arbitrary path
    # from config. Do not create it, chmod other apps, or follow an alias.
    [ -d "$expected" ] && [ ! -L "$expected" ] && [ -O "$expected" ] || { ci_error 'user applications require an existing owned ~/Applications directory'; return 1; }
    physical="$(ci_canonical_dir "$expected")" && [ "$physical" = "$expected" ] || { ci_error 'user applications directory must be physical'; return 1; }
    # %Lp omits special bits on Darwin; inspect the full octal mode instead.
    mode="$(/usr/bin/stat -f '%p' "$expected")" && [[ "$mode" =~ ^[0-7]{3,7}$ ]] && [ "$((8#$mode & 07777))" -eq "$((0700))" ] || { ci_error 'user applications directory must have mode 0700; no permissions changed'; return 1; }
    ci_owned_directory_chain "$expected" "$ci_home_path" || return 1
    CI_APPS="$expected"
}
ci_load_config() {
    local LC_ALL=C current_uid
    current_uid="$(/usr/bin/id -u)" && [[ "$current_uid" =~ ^[0-9]+$ ]] && [ "$current_uid" != 0 ] || { ci_error 'cannot establish a non-root user; do not run instance tools with sudo'; return 1; }
    CI_USER="$(/usr/bin/id -un)" && [ -n "$CI_USER" ] || { ci_error 'cannot determine current user'; return 1; }
    [ -f "$CI_CONF" ] && [ ! -L "$CI_CONF" ] && [ -O "$CI_CONF" ] || { ci_error 'instance config must be a regular file owned by this user, not a symlink'; return 1; }
    local mode
    mode="$(/usr/bin/stat -f '%Lp' "$CI_CONF")" || return 1
    [[ "$mode" =~ ^[0-7]{3,4}$ ]] && [ "$((8#$mode & 0022))" -eq 0 ] || { ci_error 'instance config is writable by another user'; return 1; }
    ci_no_allow_acl "$CI_CONF" || return 1
    local config_parent config_home
    config_parent="$(cd -- "${CI_CONF%/*}" && pwd -P)" || return 1
    config_home="$(ci_canonical_dir "$HOME")" || return 1
    case "$config_parent/" in "$config_home/"*) ;; *) ci_error 'config must be inside this user home'; return 1 ;; esac
    ci_owned_directory_chain "$config_parent" "$config_home" || return 1
    names=(); ids=(); dirs=(); hues=(); envs=(); code_dirs=()
    # Mode is config data, never an environment override. Headerless configs
    # retain the legacy policy until an explicit, reviewed transition.
    CI_MODE=legacy-copies
    CI_APP_SCOPE=system; CI_APPS="$CI_SYSTEM_APPS"
    # Links: off unless the config asks; only claude-dev, which the Desktop
    # accepts and the official app does not register. LaunchServices gives a
    # scheme to one app, so exactly one copy takes it: the one named after
    # the scheme, or the only copy when none is named.
    CI_LINKS=none; CI_LINKS_TARGET=""
    local mode_seen=no apps_seen=no links_seen=no line n bid dir hue env extra pair key value seen normalized code_dir a b
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"; line="$(ci_trim "$line")"
        [ -n "$line" ] || continue
        case "$line" in @mode*)
            [ "$mode_seen" = no ] && [ "${#names[@]}" -eq 0 ] || { ci_error 'mode must appear once before instances'; return 1; }
            case "$line" in '@mode original-copy') CI_MODE=original-copy ;; '@mode legacy-copies') CI_MODE=legacy-copies ;; *) ci_error 'unknown instance mode'; return 1 ;; esac
            mode_seen=yes; continue ;;
        @apps*)
            [ "$apps_seen" = no ] && [ "${#names[@]}" -eq 0 ] || { ci_error 'apps location must appear once before instances'; return 1; }
            case "$line" in '@apps system') CI_APP_SCOPE=system ;; '@apps user') CI_APP_SCOPE=user ;; *) ci_error 'unknown apps location; use system or user'; return 1 ;; esac
            apps_seen=yes; continue ;;
        @links*)
            [ "$links_seen" = no ] && [ "${#names[@]}" -eq 0 ] || { ci_error 'links must appear once before instances'; return 1; }
            case "$line" in '@links claude-dev') CI_LINKS=claude-dev ;; '@links none') CI_LINKS=none ;;
                '@links claude-dev '*)
                    CI_LINKS_TARGET="${line#@links claude-dev }"
                    [[ "$CI_LINKS_TARGET" =~ ^[A-Za-z][A-Za-z0-9_-]{0,63}$ ]] || { ci_error 'links name an invalid instance name'; return 1; }
                    CI_LINKS=claude-dev; CI_LINKS_TARGET="$(ci_lower "$CI_LINKS_TARGET")" ;;
                *) ci_error 'unknown links scheme; use claude-dev [name] or none'; return 1 ;; esac
            links_seen=yes; continue ;;
        esac
        IFS='|' read -r n bid dir hue env extra <<<"$line"
        [ -z "${extra:-}" ] && [[ "$line" != *'|'*'|'*'|'*'|'*'|'* ]] || { ci_error 'too many config columns'; return 1; }
        n="$(ci_trim "$n")"; bid="$(ci_trim "$bid")"; dir="$(ci_trim "$dir")"
        hue="$(ci_trim "${hue:-212}")"; env="$(ci_trim "${env:-}")"
        [[ "$n" =~ ^[A-Za-z][A-Za-z0-9_-]{0,63}$ ]] || { ci_error 'invalid instance name'; return 1; }
        [[ "$bid" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] && [ "$(ci_lower "$bid")" != com.anthropic.claudefordesktop ] || { ci_error 'invalid or reserved bundle id'; return 1; }
        [[ "$hue" =~ ^[0-9]{1,3}$ ]] && [ "$((10#$hue))" -le 359 ] || { ci_error 'hue must be an integer from 0 to 359'; return 1; }
        dir="$(ci_canonical_dir "$dir")" && ci_safe_data_dir "$dir" desktop || return 1
        code_dir=""
        seen='|'; normalized=""
        local pairs=()
        IFS=';' read -ra pairs <<<"$env"
        for pair in "${pairs[@]-}"; do
            pair="$(ci_trim "$pair")"; [ -n "$pair" ] || continue
            [[ "$pair" == *=* ]] || { ci_error 'environment entry must be KEY=VALUE'; return 1; }
            key="${pair%%=*}"; value="${pair#*=}"
            [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { ci_error 'invalid environment variable name'; return 1; }
            case "$key" in CLAUDE_CONFIG_DIR) ;;
                *) ci_error 'subscription profiles only allow CLAUDE_CONFIG_DIR; provider, credential and runtime overrides are refused'; return 1 ;;
            esac
            [[ "$seen" != *"|$key|"* ]] || { ci_error 'duplicate environment variable'; return 1; }
            seen="$seen$key|"; value="$(ci_expand "$value")" || return 1
            case "$key" in CLAUDE_CONFIG_DIR)
                value="$(ci_canonical_dir "$value")" && ci_safe_data_dir "$value" code || return 1
                code_dir="$value" ;;
            esac
            normalized="${normalized}${normalized:+;}$key=$value"
        done
        if [ -z "$code_dir" ]; then
            code_dir="$(ci_canonical_dir "$HOME/.claude")" && ci_safe_data_dir "$code_dir" code || return 1
        fi
        names+=("$n"); ids+=("$bid"); dirs+=("$dir"); hues+=("$hue"); envs+=("$normalized"); code_dirs+=("$code_dir")
    done < "$CI_CONF"
    [ "${#names[@]}" -gt 0 ] || { ci_error 'no instances configured'; return 1; }
    if [ "$CI_APP_SCOPE" = user ]; then ci_user_app_directory "$config_home" || return 1; fi
    [ "$CI_LINKS" = none ] || [ "$CI_MODE" = original-copy ] || { ci_error 'links require original-copy mode'; return 1; }
    if [ "$CI_LINKS" = claude-dev ]; then
        if [ -z "$CI_LINKS_TARGET" ]; then
            [ "${#names[@]}" -eq 1 ] || { ci_error 'with several copies, name the one that takes links: @links claude-dev <name>'; return 1; }
            CI_LINKS_TARGET="$(ci_lower "${names[0]}")"
        else
            local linked=no
            for a in "${!names[@]}"; do [ "$(ci_lower "${names[$a]}")" != "$CI_LINKS_TARGET" ] || linked=yes; done
            [ "$linked" = yes ] || { ci_error 'links name an instance that is not configured'; return 1; }
        fi
    fi
    if [ "$CI_MODE" = original-copy ]; then
        local stock_desktop stock_code
        stock_desktop="$(ci_canonical_dir "$HOME/Library/Application Support/Claude")" && stock_code="$(ci_canonical_dir "$HOME/.claude")" || return 1
        for a in "${!names[@]}"; do
            if ci_paths_overlap "${dirs[$a]}" "$stock_desktop" || ci_paths_overlap "${dirs[$a]}" "$stock_code" || ci_paths_overlap "${code_dirs[$a]}" "$stock_desktop" || ci_paths_overlap "${code_dirs[$a]}" "$stock_code"; then
                ci_error 'default Desktop and Code profiles are reserved for the original Claude'; return 1
            fi
        done
    fi
    for a in "${!names[@]}"; do
        ci_paths_overlap "${dirs[$a]}" "${code_dirs[$a]}" && { ci_error 'Desktop and Code directories overlap'; return 1; }
        for b in "${!names[@]}"; do
            [ "$a" -lt "$b" ] || continue
            [ "$(ci_lower "${names[$a]}")" != "$(ci_lower "${names[$b]}")" ] || { ci_error 'duplicate instance name'; return 1; }
            [ "$(ci_lower "${ids[$a]}")" != "$(ci_lower "${ids[$b]}")" ] || { ci_error 'duplicate bundle id'; return 1; }
            if ci_paths_overlap "${dirs[$a]}" "${dirs[$b]}" || ci_paths_overlap "${code_dirs[$a]}" "${code_dirs[$b]}" || ci_paths_overlap "${dirs[$a]}" "${code_dirs[$b]}" || ci_paths_overlap "${code_dirs[$a]}" "${dirs[$b]}"; then
                ci_error 'profile directories overlap (including nested paths and case aliases); use separate Desktop and Code directories'; return 1
            fi
        done
    done
    return 0
}
# True when the instance at this index takes the claude-dev:// links.
ci_links_for() { [ "$CI_LINKS" = claude-dev ] && [ "$(ci_lower "${names[$1]}")" = "$CI_LINKS_TARGET" ]; }
ci_app_path() { printf '%s/Claude %s.app\n' "$CI_APPS" "$1"; }
ci_version() { "$CI_PB" -c 'Print :CFBundleShortVersionString' "$1/Contents/Info.plist" 2>/dev/null; }
# 0=running, 1=not running, 2=unknown. Observation errors must stop mutations.
ci_app_running() {
    if [ -n "${CI_TX_HELPER:-}" ]; then
        [ -f "$CI_TX_HELPER" ] && [ ! -L "$CI_TX_HELPER" ] && [ -x "$CI_TX_HELPER" ] && [ -O "$CI_TX_HELPER" ] || return 2
        "$CI_TX_HELPER" app-state "$1"; return $?
    fi
    local snapshot pid executable line
    snapshot="$(ps -Aww -o pid=,comm=)" || return 2
    while IFS= read -r line; do
        read -r pid executable <<<"$line"
        [ "$executable" != "$1/Contents/MacOS/Claude" ] || return 0
        [ "$executable" != "$1/Contents/MacOS/launcher" ] || return 0
    done <<<"$snapshot"
    return 1
}
ci_require_stopped() {
    local rc
    ci_app_running "$1"; rc=$?
    case "$rc" in 1) return 0 ;; 0) ci_error "quit $1 with Cmd-Q first" ;; *) ci_error 'cannot inspect processes; refusing to continue' ;; esac
    return 1
}
ci_require_desktop_idle() {
    local state
    [ -n "${CI_TX_HELPER:-}" ] && [ -f "$CI_TX_HELPER" ] && [ ! -L "$CI_TX_HELPER" ] && [ -x "$CI_TX_HELPER" ] && [ -O "$CI_TX_HELPER" ] || { ci_error 'native lifecycle helper required'; return 1; }
    "$CI_TX_HELPER" desktop-idle; state=$?
    case "$state" in 1) return 0 ;; 0) ci_error 'quit all Claude Desktop apps and helpers first' ;; *) ci_error 'native process observation unknown; refusing maintenance' ;; esac
    return 1
}
ci_profile_of_pid() {
    local args value
    args="$(ps -ww -o command= -p "$1")" || return 2
    [ -n "$args" ] || return 2
    if [[ "$args" == *--user-data-dir* ]]; then
        value="${args#*--user-data-dir}"; value="${value#=}"; value="${value# }"
        case "$value" in *' --'*) value="${value%% --*}" ;; esac
    else
        value="$HOME/Library/Application Support/Claude"
    fi
    [ -d "$value" ] || return 2
    ci_canonical_dir "$value"
}
ci_profile_in_use() {
    local snapshot pid executable line profile
    snapshot="$(ps -Aww -o pid=,comm=)" || return 2
    while IFS= read -r line; do
        read -r pid executable <<<"$line"
        case "$executable" in */Contents/MacOS/Claude) ;; *) continue ;; esac
        profile="$(ci_profile_of_pid "$pid")" || return 2
        [ "$profile" != "$1" ] || return 0
    done <<<"$snapshot"
    return 1
}
ci_render_launcher() {
    local idx="$1"
    printf '#!/bin/bash -p\nset -euo pipefail\numask 077\n'
    [ "$CI_MODE" != original-copy ] || printf '# profile policy: original-copy\n'
    [ "$CI_APP_SCOPE" != user ] || printf '# application location: private user Applications\n'
    printf '%s\n' 'HERE="$(cd "${BASH_SOURCE[0]%/*}" && pwd -P)"'
    printf 'DATA_DIR=%q\n' "${dirs[$idx]}"
    printf 'CODE_DIR=%q\n' "${code_dirs[$idx]}"
    # This mode supports launching from Finder/Dock, not arbitrary CLI flags or
    # URL callbacks. Refuse every argument until a specific input is supported.
    printf '[ "$#" -eq 0 ] || { echo "launcher arguments are not supported" >&2; exit 1; }\n'
    # Do not silently strip a required corporate proxy and connect directly.
    printf '%s\n' '[ -z "${HTTPS_PROXY:-}${HTTP_PROXY:-}${ALL_PROXY:-}${https_proxy:-}${http_proxy:-}${all_proxy:-}" ] || { echo "proxy environment detected; this subscription launcher does not support proxy overrides; use your approved Claude setup" >&2; exit 1; }'
    printf '[ -d "$DATA_DIR" ] && [ ! -L "$DATA_DIR" ] && [ -d "$CODE_DIR" ] && [ ! -L "$CODE_DIR" ] || { echo "profile directories missing or changed; rebuild required" >&2; exit 1; }\n'
    printf '[ "$(cd "$DATA_DIR" && pwd -P)" = "$DATA_DIR" ] && [ "$(cd "$CODE_DIR" && pwd -P)" = "$CODE_DIR" ] || { echo "profile path changed; refusing launch" >&2; exit 1; }\n'
    declare -f ci_error ci_no_allow_acl ci_owned_directory_chain
    printf 'ci_owned_directory_chain "$DATA_DIR" %q\nci_owned_directory_chain "$CODE_DIR" %q\n' "$(ci_canonical_dir "$HOME")" "$(ci_canonical_dir "$HOME")"
    printf '/bin/chmod 700 "$DATA_DIR" "$CODE_DIR"\n'
    # /bin/bash -p ignores inherited BASH_ENV and exported functions. env -i
    # prevents provider/credential/loader values reaching the child. HOME stays
    # the actual configured user's home; no token is written into the launcher.
    printf 'exec /usr/bin/env -i HOME=%q USER=%q LOGNAME=%q PATH=/usr/bin:/bin:/usr/sbin:/sbin SHELL=/bin/zsh LANG=en_US.UTF-8 CLAUDE_CONFIG_DIR="$CODE_DIR" ' "$HOME" "$CI_USER" "$CI_USER"
    if [ "$(uname -m)" = arm64 ]; then
        printf '/usr/bin/arch -arm64 "$HERE/Claude" --user-data-dir="$DATA_DIR"\n'
    else
        printf '"$HERE/Claude" --user-data-dir="$DATA_DIR"\n'
    fi
}
ci_compile_integrity() {
    local build_dir="$1" bin_dir
    ci_prepare_private_directory "$CI_ICONS/integrity" || return 1
    bin_dir="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)" || return 1
    [ -f "$bin_dir/claude-integrity.c" ] && [ ! -L "$bin_dir/claude-integrity.c" ] || { ci_error 'missing integrity source; install the complete bin/claude-* set'; return 1; }
    /usr/bin/clang -std=c11 -Wall -Wextra -Werror -O2 -mmacosx-version-min=12.0 -framework CoreFoundation "$bin_dir/claude-integrity.c" -o "$build_dir/integrity" || { ci_error 'integrity helper compilation failed'; return 1; }
    CI_INTEGRITY_HELPER="$build_dir/integrity"; export CI_INTEGRITY_HELPER
}
ci_integrity_binding() {
    local idx="$1" result policy=MCD_BUILD_CONFIG_1
    [ "$CI_MODE" != original-copy ] || policy=MCD_BUILD_CONFIG_ORIGINAL_COPY_3
    result="$(
        {
            [ "$CI_APP_SCOPE" != user ] || printf '%s\0' MCD_USER_APPLICATIONS_1 "$CI_APPS"
            ! ci_links_for "$idx" || printf '%s\0' MCD_LINKS_1 "$CI_LINKS"
            printf '%s\0' "$policy" "${names[$idx]}" "${ids[$idx]}" "${dirs[$idx]}" "${code_dirs[$idx]}" "${hues[$idx]}"
        } | /usr/bin/shasum -a 256
    )" || return 1
    printf '%s\n' "${result%% *}"
}
ci_integrity_helper_valid() {
    [ -n "${CI_INTEGRITY_HELPER:-}" ] && [ -f "$CI_INTEGRITY_HELPER" ] && [ -x "$CI_INTEGRITY_HELPER" ] && [ ! -L "$CI_INTEGRITY_HELPER" ] && [ -O "$CI_INTEGRITY_HELPER" ] || { ci_error 'compiled integrity helper required; use the public entry script'; return 1; }
}
# Re-run the native integrity check, bounded, only when it reports that app
# metadata moved during observation (exit 75); every attempt is a full check.
ci_integrity_settled() {
    local attempt=1 rc
    while :; do
        "$CI_INTEGRITY_HELPER" "$@" && return 0
        rc=$?
        [ "$rc" -eq 75 ] && [ "$attempt" -lt 3 ] || return 1
        printf 'integrity: app metadata changed during observation; repeating the full check (%s/3)\n' "$((attempt + 1))" >&2
        /bin/sleep 2
        attempt=$((attempt + 1))
    done
}
ci_verify_one() {
    local app="$1" idx="$2" id executable
    [ -d "$app" ] && [ ! -L "$app" ] || { ci_error "missing or symlinked app: $app"; return 1; }
    id="$("$CI_PB" -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")" || return 1
    executable="$("$CI_PB" -c 'Print :CFBundleExecutable' "$app/Contents/Info.plist")" || return 1
    [ "$id" = "${ids[$idx]}" ] && [ "$executable" = launcher ] || { ci_error 'bundle identity changed'; return 1; }
    [ -f "$app/Contents/MacOS/launcher" ] && [ ! -L "$app/Contents/MacOS/launcher" ] && [ -x "$app/Contents/MacOS/launcher" ] || return 1
    # Compare bytes, never evaluate or run the inspected launcher.
    cmp -s "$app/Contents/MacOS/launcher" <(ci_render_launcher "$idx") || { ci_error 'launcher differs from expected configuration'; return 1; }
    codesign --verify --deep --strict --all-architectures "$app" || return 1
    ci_integrity_helper_valid || return 1
    ci_integrity_settled verify "$CI_ICONS/integrity" "$app" "$(ci_integrity_binding "$idx")" || return 1
}

ci_status() {
    local i app version source_version
    source_version="$(ci_version "$CI_SRC")" || source_version=unavailable
    printf 'Managed applications (%s): %s\n' "$CI_APP_SCOPE" "$CI_APPS"
    printf '  %-24s %s\n' 'Claude.app (source)' "$source_version"
    for i in "${!names[@]}"; do
        app="$(ci_app_path "${names[$i]}")"
        version="$(ci_version "$app")" || version='NOT BUILT'
        printf '  %-24s %-14s' "Claude ${names[$i]}" "$version"
        if [ "$version" = "$source_version" ]; then printf 'same version\n'; else printf 'rebuild required\n'; fi
    done
    printf 'Version comparison only; authenticity, GUI compatibility and account separation are not established by this status.\n'
}
