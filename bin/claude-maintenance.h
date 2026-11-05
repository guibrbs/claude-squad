// Included by the transaction helper: process metadata and cache-only mutation.
// No argv/environment inspection, token access, shell evaluation or path rm -rf.
#include <dirent.h>
#include <libproc.h>
#include <sys/proc.h>
#include <pwd.h>

static bool maintenance_gone(pid_t pid) { return kill(pid, 0) < 0 && errno == ESRCH; }
static bool maintenance_match(const char *executable, const char *app) {
    if (app) {
        size_t size = strlen(app);
        return !strncmp(executable, app, size) && !strncmp(executable + size, "/Contents/", 10);
    }
    const char *binary = strstr(executable, "/Contents/MacOS/");
    if (!binary) return false;
    binary += strlen("/Contents/MacOS/");
    return !strcmp(binary, "Claude") || !strncmp(binary, "Claude Helper", 13) || !strcmp(binary, "launcher");
}
// The name the kernel keeps for a process, with non-printable bytes masked.
#define MAINTENANCE_NAME_SIZE (sizeof(((struct proc_bsdinfo *)0)->pbi_name) + 1)
static void maintenance_name(const struct proc_bsdinfo *info, char name[static MAINTENANCE_NAME_SIZE]) {
    size_t i = 0;
    for (; i < sizeof(info->pbi_name) && info->pbi_name[i]; i++)
        name[i] = info->pbi_name[i] >= ' ' && info->pbi_name[i] <= '~' ? info->pbi_name[i] : '?';
    name[i] = '\0';
}
// Names a process whose executable path cannot be read. An old helper kept
// alive after Claude updated itself runs a deleted file: the path is gone but
// the kernel keeps the name it was started with.
static void maintenance_report(pid_t pid, const struct proc_bsdinfo *info, int error) {
    char name[MAINTENANCE_NAME_SIZE]; maintenance_name(info, name);
    if (error == ENOENT) fprintf(stderr, "process %d (%s) runs a deleted executable; quit it and retry\n", (int)pid, name);
    else fprintf(stderr, "process %d (%s) cannot be observed: %s\n", (int)pid, name, strerror(error));
}
// Names a process that changed between two reads (exec, exit and PID reuse).
static void maintenance_report_changed(pid_t pid, const struct proc_bsdinfo *info) {
    char name[MAINTENANCE_NAME_SIZE]; maintenance_name(info, name);
    fprintf(stderr, "process %d (%s) changed while observed\n", (int)pid, name);
}
// One pass over the user's processes: 0=running, 1=none observed, 2=unknown.
// A process running a deleted executable stays unknown until someone quits it,
// so it sets *lasting and is always named; the other unknowns are races with
// processes starting or exiting, named only when report is set (last pass).
static int maintenance_process_scan(const char *app, bool report, bool *lasting) {
#ifdef MCD_PROCESS_FIXTURE
    static int fixture_passes = 0;
    const char *fixture_failures = getenv("MOCK_PS_FAIL_PASSES");
    if (fixture_failures && fixture_passes++ < atoi(fixture_failures)) {
        if (report) fprintf(stderr, "process 4242 (fixture) changed while observed\n");
        return 2;
    }
    if (getenv("MOCK_PS_FAIL")) return 2;
    const char *fixture_running = getenv("MOCK_RUNNING_APP");
    return fixture_running && *fixture_running && (!app || !strcmp(app, fixture_running)) ? 0 : 1;
#endif
    if (app && (app[0] != '/' || strlen(app) >= PROC_PIDPATHINFO_MAXSIZE - 16)) { *lasting = true; return 2; }
    int needed = proc_listpids(PROC_UID_ONLY, getuid(), NULL, 0);
    if (needed <= 0 || needed > 16 * 1024 * 1024) return 2;
    int capacity = needed + (int)(512 * sizeof(pid_t));
    pid_t *pids = calloc(1, (size_t)capacity); if (!pids) return 2;
    int used = proc_listpids(PROC_UID_ONLY, getuid(), pids, capacity);
    if (used <= 0 || used >= capacity || used % (int)sizeof(pid_t)) { free(pids); return 2; }
    bool saw_self = false, running = false; int result = 1;
    for (size_t i = 0; i < (size_t)used / sizeof(pid_t); i++) {
        pid_t pid = pids[i]; if (pid <= 0) continue;
        struct proc_bsdinfo before, after;
        if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &before, sizeof(before)) != sizeof(before)) {
            int error = errno;
            if (maintenance_gone(pid)) continue;
            if (report) fprintf(stderr, "process %d cannot be observed: %s\n", (int)pid, strerror(error));
            result = 2; break;
        }
        if (before.pbi_status == SZOMB) continue;
        if (before.pbi_pid != (uint32_t)pid || before.pbi_uid != getuid()) {
            if (report) maintenance_report_changed(pid, &before);
            result = 2; break;
        }
        char first[PROC_PIDPATHINFO_MAXSIZE], second[PROC_PIDPATHINFO_MAXSIZE];
        memset(first, 0, sizeof(first)); memset(second, 0, sizeof(second));
        if (proc_pidpath(pid, first, sizeof(first)) <= 0 || proc_pidpath(pid, second, sizeof(second)) <= 0 ||
            proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &after, sizeof(after)) != sizeof(after)) {
            int error = errno;
            // Still unknown; keep scanning only to name every such process.
            if (maintenance_gone(pid)) continue;
            if (error == ENOENT) *lasting = true;
            if (report || error == ENOENT) maintenance_report(pid, &before, error);
            result = 2; continue;
        }
        if (after.pbi_status == SZOMB) continue;
        if (first[0] != '/' || !memchr(first, '\0', sizeof(first)) || !memchr(second, '\0', sizeof(second)) ||
            strcmp(first, second) || before.pbi_pid != after.pbi_pid || before.pbi_uid != after.pbi_uid ||
            before.pbi_start_tvsec != after.pbi_start_tvsec || before.pbi_start_tvusec != after.pbi_start_tvusec) {
            if (report) maintenance_report_changed(pid, &before);
            result = 2; break;
        }
        if (pid == getpid()) saw_self = true;
        if (maintenance_match(first, app)) running = true;
    }
    free(pids);
    if (result != 2 && !saw_self && report) fprintf(stderr, "own process missing from the process list\n");
    if (result == 2 || !saw_self) return 2; return running ? 0 : 1;
}
// Right after a build the system is busy with the new bundle (code signing,
// Gatekeeper, Spotlight) and a pass can catch a process mid-exec. An unknown
// pass is repeated a few times; it never turns into "none observed" unless a
// later full pass observes every process.
#ifdef MCD_PROCESS_FIXTURE
#define MAINTENANCE_SETTLE_USEC 10000
#else
#define MAINTENANCE_SETTLE_USEC 2000000
#endif
#define MAINTENANCE_PASSES 3
// 0=running, 1=none observed, 2=unknown. Unknown can never authorize mutation.
static int maintenance_process_state(const char *app) {
    for (int pass = 1;; pass++) {
        bool lasting = false;
        int state = maintenance_process_scan(app, pass == MAINTENANCE_PASSES, &lasting);
        if (state != 2 || lasting || pass == MAINTENANCE_PASSES) return state;
        usleep(MAINTENANCE_SETTLE_USEC);
    }
}
static void cache_hook(const char *point) {
#ifdef MCD_TRANSACTION_TESTING
    const char *stop = getenv("MCD_CACHE_STOP"); if (stop && !strcmp(stop, point)) raise(SIGSTOP);
#else
    (void)point;
#endif
}
static const char *cache_names[] = {"Cache", "Code Cache", "GPUCache", "DawnWebGPUCache", "DawnGraphiteCache", "Shared Dictionary"};
static void cache_metadata(int fd, struct stat value, dev_t device) {
    if (value.st_uid != getuid() || value.st_dev != device || (value.st_mode & 07022) || value.st_flags ||
        (!S_ISDIR(value.st_mode) && !S_ISREG(value.st_mode)) || (S_ISREG(value.st_mode) && value.st_nlink != 1))
        fail("unsafe cache entry; links, special files, flags, foreign/writable entries and mounts are refused");
    check_acl(fd);
}
static bool cache_same(struct stat a, struct stat b) {
    return same(identify(a), identify(b)) && a.st_mode == b.st_mode && a.st_uid == b.st_uid &&
        (!S_ISREG(a.st_mode) || a.st_nlink == b.st_nlink) && a.st_flags == b.st_flags;
}
static int cache_open_child(int parent, const char *name, dev_t device, struct stat *value) {
    if (fstatat(parent, name, value, AT_SYMLINK_NOFOLLOW)) system_fail("cannot inspect cache child");
    if (!S_ISDIR(value->st_mode) && !S_ISREG(value->st_mode)) fail("linked or special cache child; nothing follows links");
    int fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0 || !cache_same(*value, descriptor_stat(fd))) fail("cache child changed while opening");
    cache_metadata(fd, *value, device); return fd;
}
static void cache_walk(int fd, dev_t device, unsigned depth, size_t *entries, bool remove) {
    if (depth > 128 || ++*entries > 1000000) fail("cache depth/entry limit exceeded; quarantine retained");
    struct stat current = descriptor_stat(fd); cache_metadata(fd, current, device);
    if (!S_ISDIR(current.st_mode)) return;
    int duplicate = dup(fd); if (duplicate < 0) system_fail("cannot duplicate cache directory");
    DIR *directory = fdopendir(duplicate); if (!directory) system_fail("cannot enumerate cache directory");
    rewinddir(directory);
    for (;;) {
        errno = 0; struct dirent *entry = readdir(directory);
        if (!entry) { if (errno) system_fail("cache enumeration failed"); break; }
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        struct stat named; int child = cache_open_child(fd, entry->d_name, device, &named);
        cache_walk(child, device, depth + 1, entries, remove);
        struct stat after;
        if (fstatat(fd, entry->d_name, &after, AT_SYMLINK_NOFOLLOW) || !cache_same(named, after) || !cache_same(after, descriptor_stat(child)))
            fail("cache child identity changed; quarantine retained");
        if (remove && unlinkat(fd, entry->d_name, S_ISDIR(named.st_mode) ? AT_REMOVEDIR : 0)) system_fail("cache deletion failed; quarantine retained");
        close(child);
    }
    if (closedir(directory)) system_fail("cannot close cache enumeration");
}
static void cache_profile_named(int fd, const char *path) {
    int named = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC);
    if (named < 0 || !cache_same(descriptor_stat(fd), descriptor_stat(named))) fail("profile path changed; refusing cache mutation");
    close(named);
}
static void cache_require_idle(void) {
    int state = maintenance_process_state(NULL);
    if (state != 1) fail(state == 0 ? "quit all Claude Desktop processes/helpers before cache mutation" : "native process observation unknown; caches unchanged/quarantine retained");
}
static bool cache_allowed_profile(const char *path) {
    const char *user_path = NULL;
#ifdef MCD_PROCESS_FIXTURE
    user_path = getenv("MULTI_CLAUDE_TEST_HOME");
#endif
    if (!user_path) { struct passwd *account = getpwuid(getuid()); if (!account) return false; user_path = account->pw_dir; }
    char user_root[PATH_MAX], base[PATH_MAX], physical[PATH_MAX];
    if (!realpath(user_path, user_root) || !realpath(path, physical) || strcmp(path, physical)) return false;
    int size = snprintf(base, sizeof(base), "%s/Library/Application Support/Claude", user_root);
    if (size < 0 || (size_t)size >= sizeof(base)) return false;
    if (!strcmp(path, base)) return true;
    size_t length = strlen(base);
    if (!strncmp(path, base, length) && path[length] == '-' && path[length + 1]) {
        const unsigned char *leaf = (const unsigned char *)path + length + 1;
        for (; *leaf; leaf++) if (*leaf < ' ' || *leaf > '~' || *leaf == '/') return false;
        return true;
    }
    size = snprintf(base, sizeof(base), "%s/Library/Application Support/Multi Claude Desktop/Profiles/", user_root);
    if (size < 0 || (size_t)size >= sizeof(base)) return false;
    length = strlen(base); if (strncmp(path, base, length)) return false;
    const char *id = path + length; if (strlen(id) != 44 || strcmp(id + 36, "/desktop")) return false;
    for (size_t i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (id[i] != '-') return false; }
        else if (!strchr("0123456789abcdefABCDEF", id[i])) return false;
    }
    return true;
}
static void cache_clean(const char *profile_path) {
    if (profile_path[0] != '/' || !cache_allowed_profile(profile_path)) fail("cache cleanup only accepts canonical managed/legacy Desktop profiles in this user's home");
    int profile = open(profile_path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC);
    if (profile < 0) system_fail("cannot open cache profile without symlinks");
    struct stat profile_info = descriptor_stat(profile); cache_metadata(profile, profile_info, profile_info.st_dev);
    cache_require_idle();
    struct stat expected[sizeof(cache_names) / sizeof(cache_names[0])];
    bool present[sizeof(cache_names) / sizeof(cache_names[0])] = {false};
    size_t entries = 0, count = 0;
    for (size_t i = 0; i < sizeof(cache_names) / sizeof(cache_names[0]); i++) {
        if (fstatat(profile, cache_names[i], &expected[i], AT_SYMLINK_NOFOLLOW)) {
            if (errno == ENOENT) continue; system_fail("cannot inspect cache slot");
        }
        int fd = cache_open_child(profile, cache_names[i], profile_info.st_dev, &expected[i]);
        cache_walk(fd, profile_info.st_dev, 0, &entries, false); close(fd); present[i] = true; count++;
    }
    if (!count) { printf("No known caches to remove.\n"); return; }
    cache_hook("checked"); cache_profile_named(profile, profile_path); cache_require_idle();
    char quarantine[48]; unsigned char random[12]; arc4random_buf(random, sizeof(random));
    memcpy(quarantine, ".claude-cache.", 14);
    for (size_t i = 0; i < sizeof(random); i++) snprintf(quarantine + 14 + 2 * i, 3, "%02x", random[i]);
    if (mkdirat(profile, quarantine, 0700)) system_fail("cannot exclusively create cache quarantine");
    int stage = openat(profile, quarantine, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (stage < 0) system_fail("cannot open cache quarantine");
    cache_metadata(stage, descriptor_stat(stage), profile_info.st_dev);
    printf("Cache quarantine: %s (retained on uncertainty; no automatic replay).\n", quarantine); fflush(stdout);
    sync_descriptor(profile);
    for (size_t i = 0; i < sizeof(cache_names) / sizeof(cache_names[0]); i++) {
        if (!present[i]) continue;
        cache_require_idle(); cache_profile_named(profile, profile_path);
        struct stat named;
        if (fstatat(profile, cache_names[i], &named, AT_SYMLINK_NOFOLLOW) || !cache_same(named, expected[i])) fail("cache slot changed; quarantine retained");
        if (renameatx_np(profile, cache_names[i], stage, cache_names[i], RENAME_EXCL | RENAME_NOFOLLOW_ANY)) system_fail("cache quarantine rename failed; no fallback");
        sync_descriptor(stage); sync_descriptor(profile); cache_hook("quarantined");
        int fd = cache_open_child(stage, cache_names[i], profile_info.st_dev, &named);
        if (!cache_same(named, expected[i])) fail("quarantined cache identity mismatch; no deletion");
        cache_profile_named(profile, profile_path); cache_require_idle(); entries = 0;
        cache_walk(fd, profile_info.st_dev, 0, &entries, true);
        struct stat after;
        if (fstatat(stage, cache_names[i], &after, AT_SYMLINK_NOFOLLOW) || !cache_same(after, descriptor_stat(fd))) fail("quarantined cache changed; retained");
        if (unlinkat(stage, cache_names[i], S_ISDIR(named.st_mode) ? AT_REMOVEDIR : 0)) system_fail("cannot remove empty cache slot");
        close(fd); sync_descriptor(stage);
    }
    cache_profile_named(profile, profile_path);
    struct stat named;
    if (fstatat(profile, quarantine, &named, AT_SYMLINK_NOFOLLOW) || !cache_same(named, descriptor_stat(stage))) fail("quarantine root changed");
    if (unlinkat(profile, quarantine, AT_REMOVEDIR)) system_fail("cannot remove empty cache quarantine");
    sync_descriptor(profile); close(stage); close(profile); printf("Known caches removed through pinned descriptors.\n");
}
