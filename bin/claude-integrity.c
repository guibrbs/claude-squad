// Local build/content records, macOS 12+. Never runs app/profile code.
// A user-owned record detects drift, not an attacker able to rewrite the record.
#include <CommonCrypto/CommonDigest.h>
#include <CoreFoundation/CoreFoundation.h>
#include <sys/acl.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MARKER "Contents/Resources/mcd-build-id"
#define ASAR "Contents/Resources/app.asar"
#define ASAR_TEMP "Contents/Resources/.app.asar.mcd-new"
// Electron 44.9 seals the ElectronAsarIntegrity dictionary into the framework
// (shell/common/asar/integrity_digest.mm): a SHA-256 slot that must follow
// the Info.plist hash, so the copy's framework and its helpers are re-signed.
#define FRAMEWORK_DIR "Contents/Frameworks/Electron Framework.framework/Versions/A/"
#define FRAMEWORK FRAMEWORK_DIR "Electron Framework"
#define DIGEST_SENTINEL "AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
struct hashes { unsigned char full[32], unchanged[32]; };
struct walk { const char *root; dev_t device; bool source, icon, product, code; size_t entries; };
static void fail(const char *message) { fprintf(stderr, "integrity: %s\n", message); exit(1); }
// Metadata moved while observing (e.g. macOS adding com.apple.macl right after
// publication). Exit 75 lets callers re-run the whole check a bounded number
// of times; content mismatches always exit 1 and are never retried.
#define UNSTABLE_EXIT 75
static void unstable(const char *message) { fprintf(stderr, "integrity: %s\n", message); exit(UNSTABLE_EXIT); }
static void syserr(const char *message) { fprintf(stderr, "integrity: %s: %s\n", message, strerror(errno)); exit(1); }
static struct stat info(int fd) { struct stat s; if (fstat(fd, &s)) syserr("cannot inspect descriptor"); return s; }
static bool same(struct stat a, struct stat b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_mode == b.st_mode && a.st_uid == b.st_uid &&
        a.st_gid == b.st_gid && a.st_nlink == b.st_nlink && a.st_size == b.st_size && a.st_flags == b.st_flags &&
        a.st_mtimespec.tv_sec == b.st_mtimespec.tv_sec && a.st_mtimespec.tv_nsec == b.st_mtimespec.tv_nsec &&
        a.st_ctimespec.tv_sec == b.st_ctimespec.tv_sec && a.st_ctimespec.tv_nsec == b.st_ctimespec.tv_nsec;
}
static void acl_check(int fd) {
    acl_t acl = acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (!acl) { if (errno == ENOENT) return; syserr("cannot inspect ACL"); }
    acl_entry_t entry; int rc = acl_get_entry(acl, ACL_FIRST_ENTRY, &entry);
    while (rc == 0) {
        acl_tag_t tag;
        if (acl_get_tag_type(entry, &tag) || tag != ACL_EXTENDED_DENY) fail("custom allow/unknown ACL");
        rc = acl_get_entry(acl, ACL_NEXT_ENTRY, &entry);
    }
    int saved = errno; acl_free(acl);
    if (rc != -1 || saved != EINVAL) fail("unknown ACL enumeration result");
}
static void metadata(int fd, struct stat s, struct walk *walk) {
    if ((s.st_uid != getuid() && !(walk->source && s.st_uid == 0)) || s.st_dev != walk->device || (s.st_mode & 0022))
        fail("foreign, writable or cross-device app entry");
    if (!S_ISREG(s.st_mode) && !S_ISDIR(s.st_mode)) fail("unsupported app entry");
    if (S_ISREG(s.st_mode) && s.st_nlink != 1) fail("hardlinked app file");
    acl_check(fd);
}
static void update(CC_SHA256_CTX *c, const void *p, size_t length) {
    if (length > UINT32_MAX || !CC_SHA256_Update(c, p, (CC_LONG)length)) fail("hash update failed");
}
static void number(CC_SHA256_CTX *c, uint64_t n) {
    unsigned char bytes[8]; for (int i = 7; i >= 0; i--) { bytes[i] = (unsigned char)n; n >>= 8; } update(c, bytes, 8);
}
static void start(CC_SHA256_CTX *c, const char *type, mode_t mode) {
    if (!CC_SHA256_Init(c)) fail("hash initialization failed");
    update(c, "MCD_TREE_2", 10); update(c, type, 1); number(c, mode & 07777);
}
static void finish(CC_SHA256_CTX *c, unsigned char digest[32]) { if (!CC_SHA256_Final(digest, c)) fail("hash finalization failed"); }
static void hex(const unsigned char bytes[32], char text[65]) { for (size_t i = 0; i < 32; i++) snprintf(text + i * 2, 3, "%02x", bytes[i]); }
static int ordered(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
static char **names(int fd, size_t *count) {
    int copy = dup(fd); if (copy < 0) syserr("cannot duplicate directory");
    DIR *dir = fdopendir(copy); if (!dir) syserr("cannot enumerate directory");
    rewinddir(dir); *count = 0; size_t capacity = 0; char **items = NULL;
    for (;;) {
        errno = 0; struct dirent *entry = readdir(dir);
        if (!entry) { if (errno) syserr("directory read failed"); break; }
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        if (*count >= 1000000) fail("directory entry limit exceeded");
        if (*count == capacity) {
            capacity = capacity ? capacity * 2 : 32;
            char **grown = realloc(items, capacity * sizeof(*items)); if (!grown) syserr("allocation failed"); items = grown;
        }
        items[*count] = strdup(entry->d_name); if (!items[*count]) syserr("allocation failed"); (*count)++;
    }
    if (closedir(dir)) syserr("cannot close enumeration");
    if (*count) qsort(items, *count, sizeof(*items), ordered); return items;
}
static void security_attributes(int fd, CC_SHA256_CTX *c) {
    ssize_t length = flistxattr(fd, NULL, 0, 0);
    if (length < 0 || length > 1024 * 1024) fail("cannot bound extended attribute list");
    char *buffer = malloc((size_t)length + 1); if (!buffer) syserr("allocation failed");
    if (flistxattr(fd, buffer, (size_t)length, 0) != length) fail("attribute list changed");
    char **selected = calloc((size_t)length + 1, sizeof(char *)); if (!selected) syserr("allocation failed");
    size_t count = 0, offset = 0;
    while (offset < (size_t)length) {
        size_t size = strnlen(buffer + offset, (size_t)length - offset);
        if (!size || size == (size_t)length - offset) fail("invalid attribute list");
        char *name = buffer + offset;
        if (!strncmp(name, "com.apple.cs.", 13) || !strcmp(name, "com.apple.ResourceFork")) selected[count++] = name;
        offset += size + 1;
    }
    if (count) qsort(selected, count, sizeof(*selected), ordered);
    number(c, count);
    for (size_t i = 0; i < count; i++) {
        ssize_t size = fgetxattr(fd, selected[i], NULL, 0, 0, 0);
        if (size < 0 || size > 16 * 1024 * 1024) fail("cannot bound security/resource-fork attribute");
        unsigned char *value = malloc((size_t)size + 1); if (!value) syserr("allocation failed");
        if (fgetxattr(fd, selected[i], value, (size_t)size, 0, 0) != size) fail("attribute changed during read");
        number(c, strlen(selected[i])); update(c, selected[i], strlen(selected[i])); number(c, (uint64_t)size); update(c, value, (size_t)size); free(value);
    }
    free(selected); free(buffer);
}
static bool signature_file(const char *name) {
    return !strcmp(name, "CodeResources") || !strcmp(name, "CodeDirectory") || !strcmp(name, "CodeRequirements") || !strcmp(name, "CodeSignature");
}
// Contents/Frameworks/<H>.app/Contents/MacOS/<H> and its _CodeSignature files.
static bool helper_code(const char *path) {
    static const char prefix[] = "Contents/Frameworks/";
    if (strncmp(path, prefix, sizeof(prefix) - 1)) return false;
    const char *name = path + sizeof(prefix) - 1, *end = strstr(name, ".app/Contents/");
    if (!end || end == name || memchr(name, '/', (size_t)(end - name))) return false;
    const char *rest = end + 14; size_t length = (size_t)(end - name);
    if (!strncmp(rest, "_CodeSignature/", 15)) return signature_file(rest + 15);
    return !strncmp(rest, "MacOS/", 6) && strlen(rest + 6) == length && !strncmp(rest + 6, name, length);
}
static bool mutable(const char *path, bool icon, bool product, bool code) {
    if (code && (!strcmp(path, FRAMEWORK) || (!strncmp(path, FRAMEWORK_DIR "_CodeSignature/", sizeof(FRAMEWORK_DIR "_CodeSignature/") - 1) &&
        signature_file(path + sizeof(FRAMEWORK_DIR "_CodeSignature/") - 1)) || helper_code(path))) return true;
    return (product && !strcmp(path, ASAR)) || !strcmp(path, "Contents/Info.plist") || !strcmp(path, "Contents/MacOS/Claude") ||
        !strcmp(path, "Contents/MacOS/launcher") || !strcmp(path, "Contents/_CodeSignature/CodeResources") ||
        !strcmp(path, "Contents/_CodeSignature/CodeDirectory") || !strcmp(path, "Contents/_CodeSignature/CodeRequirements") ||
        !strcmp(path, "Contents/_CodeSignature/CodeSignature") ||
        !strcmp(path, MARKER) || (icon && !strcmp(path, "Contents/Resources/appicon.icns"));
}
static bool inside(const char *root, const char *path) { size_t length = strlen(root); return !strncmp(root, path, length) && path[length] == '/'; }
static void file_data(int fd, CC_SHA256_CTX *c, CC_SHA256_CTX *fixed, off_t expected) {
    unsigned char bytes[65536]; uint64_t total = 0;
    for (;;) {
        ssize_t count = read(fd, bytes, sizeof(bytes));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) syserr("cannot read app file"); if (!count) break;
        total += (uint64_t)count; if (total > (uint64_t)expected) fail("app file grew"); update(c, bytes, (size_t)count); update(fixed, bytes, (size_t)count);
    }
    if (total != (uint64_t)expected) fail("app file shrank");
}
static struct hashes visit(int fd, const char *path, struct walk *walk, unsigned depth) {
    if (depth > 128 || ++walk->entries > 1000000) fail("tree depth/entry limit exceeded");
    struct stat before = info(fd); metadata(fd, before, walk);
    CC_SHA256_CTX full, fixed; struct hashes result;
    start(&full, S_ISDIR(before.st_mode) ? "D" : "F", before.st_mode);
    start(&fixed, S_ISDIR(before.st_mode) ? "D" : "F", before.st_mode & 0700);
    security_attributes(fd, &full);
    security_attributes(fd, &fixed);
    if (S_ISREG(before.st_mode)) {
        number(&full, (uint64_t)before.st_size); number(&fixed, (uint64_t)before.st_size); file_data(fd, &full, &fixed, before.st_size);
        finish(&full, result.full); finish(&fixed, result.unchanged);
    } else {
        size_t count; char **items = names(fd, &count);
        for (size_t i = 0; i < count; i++) {
            char child_path[PATH_MAX];
            int length = snprintf(child_path, sizeof(child_path), "%s%s%s", path, *path ? "/" : "", items[i]);
            if (length < 0 || (size_t)length >= sizeof(child_path)) fail("app path too long");
            struct stat named; if (fstatat(fd, items[i], &named, AT_SYMLINK_NOFOLLOW)) syserr("cannot inspect app child");
            struct hashes child;
            if (S_ISLNK(named.st_mode)) {
                if (named.st_uid != getuid() && !(walk->source && named.st_uid == 0)) fail("foreign symlink");
                char link[PATH_MAX], absolute[PATH_MAX], resolved[PATH_MAX];
                ssize_t size = readlinkat(fd, items[i], link, sizeof(link));
                if (size <= 0 || (size_t)size >= sizeof(link) || link[0] == '/') fail("invalid/absolute bundle symlink");
                link[size] = '\0';
                length = snprintf(absolute, sizeof(absolute), "%s/%s", walk->root, child_path);
                if (length < 0 || (size_t)length >= sizeof(absolute) || !realpath(absolute, resolved) || !inside(walk->root, resolved)) fail("escaping, dangling or cyclic bundle symlink");
                CC_SHA256_CTX h; start(&h, "L", 0); number(&h, (uint64_t)size); update(&h, link, (size_t)size);
                finish(&h, child.full); memcpy(child.unchanged, child.full, 32);
            } else {
                if (!S_ISREG(named.st_mode) && !S_ISDIR(named.st_mode)) fail("special app file");
                int next = openat(fd, items[i], O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
                if (next < 0 || !same(named, info(next))) unstable("app child changed during open");
                child = visit(next, child_path, walk, depth + 1); close(next);
            }
            struct stat after; if (fstatat(fd, items[i], &after, AT_SYMLINK_NOFOLLOW) || !same(named, after)) unstable("named app child changed");
            number(&full, strlen(items[i])); update(&full, items[i], strlen(items[i])); update(&full, child.full, 32);
            if (!mutable(child_path, walk->icon, walk->product, walk->code)) { number(&fixed, strlen(items[i])); update(&fixed, items[i], strlen(items[i])); update(&fixed, child.unchanged, 32); }
            free(items[i]);
        }
        free(items); finish(&full, result.full); finish(&fixed, result.unchanged);
    }
    if (!same(before, info(fd))) unstable("app changed during tree observation"); return result;
}
static int root(const char *path, bool source, bool private) {
    if (path[0] != '/') fail("absolute path required");
    int fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC);
    if (fd < 0) syserr("cannot open root without symlinks");
    struct stat s = info(fd); struct walk walk = {.source = source, .device = s.st_dev}; metadata(fd, s, &walk);
    if (private && (s.st_mode & 07777) != 0700) fail("record directory must be private"); return fd;
}
static struct hashes tree(int fd, const char *path, bool source, bool icon, bool product, bool code) {
    struct walk walk = {.root = path, .device = info(fd).st_dev, .source = source, .icon = icon, .product = product, .code = code, .entries = 0};
    struct hashes result = visit(fd, "", &walk, 0);
    int named = root(path, source, false); if (!same(info(fd), info(named))) unstable("app root path changed"); close(named); return result;
}
static bool uuid(const char *id) {
    if (strlen(id) != 36) return false;
    for (size_t i = 0; i < 36; i++) if ((i == 8 || i == 13 || i == 18 || i == 23) ? id[i] != '-' : !strchr("0123456789abcdef", id[i])) return false;
    return true;
}
static bool digest(const char *text) { return strlen(text) == 64 && strspn(text, "0123456789abcdef") == 64; }
static size_t read_small(int parent, const char *path, char *buffer, size_t limit) {
    int fd = openat(parent, path, O_RDONLY | O_NOFOLLOW_ANY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) syserr("missing or linked build record/marker");
    struct stat before = info(fd);
    if (!S_ISREG(before.st_mode) || before.st_uid != getuid() || before.st_nlink != 1 || (before.st_mode & 0022) || before.st_size <= 0 || (uint64_t)before.st_size >= limit) fail("unsafe build record/marker");
    if (!strcmp(path, "record.txt") && (before.st_mode & 07777) != 0600) fail("build record must be mode 0600");
    acl_check(fd); size_t total = 0;
    while (total < limit) {
        ssize_t n = read(fd, buffer + total, limit - total);
        if (n < 0 && errno == EINTR) continue; if (n < 0) syserr("record read failed"); if (!n) break; total += (size_t)n;
    }
    if (total != (size_t)before.st_size || !same(before, info(fd))) fail("record changed during read");
    buffer[total] = '\0'; close(fd); return total;
}
static void marker(int app, char id[37]) {
    char bytes[64]; size_t length = read_small(app, MARKER, bytes, sizeof(bytes) - 1);
    if (length != 37 || bytes[36] != '\n') fail("invalid build marker"); memcpy(id, bytes, 36); id[36] = '\0'; if (!uuid(id)) fail("invalid build UUID");
}
static CFMutableDictionaryRef CreatePlist(int app) {
    int fd = openat(app, "Contents/Info.plist", O_RDONLY | O_NOFOLLOW_ANY | O_CLOEXEC);
    if (fd < 0) syserr("cannot open Info.plist"); struct stat before = info(fd);
    if (!S_ISREG(before.st_mode) || before.st_size <= 0 || before.st_size > 16 * 1024 * 1024) fail("invalid Info.plist size/type");
    unsigned char *bytes = malloc((size_t)before.st_size); if (!bytes) syserr("allocation failed"); size_t total = 0;
    while (total < (size_t)before.st_size) {
        ssize_t n = read(fd, bytes + total, (size_t)before.st_size - total);
        if (n < 0 && errno == EINTR) continue; if (n <= 0) fail("partial plist read"); total += (size_t)n;
    }
    if (!same(before, info(fd))) fail("plist changed during read"); close(fd);
    CFDataRef data = CFDataCreate(NULL, bytes, (CFIndex)total); free(bytes); if (!data) fail("plist allocation failed");
    CFPropertyListRef value = CFPropertyListCreateWithData(NULL, data, kCFPropertyListMutableContainersAndLeaves, NULL, NULL); CFRelease(data);
    if (!value || CFGetTypeID(value) != CFDictionaryGetTypeID()) fail("Info.plist must be a dictionary"); return (CFMutableDictionaryRef)value;
}
static void set_string(CFMutableDictionaryRef d, CFStringRef key, const char *text) {
    CFStringRef value = CFStringCreateWithCString(NULL, text, kCFStringEncodingUTF8); if (!value) fail("invalid metadata string"); CFDictionarySetValue(d, key, value); CFRelease(value);
}
static void planned_metadata(int source, int app, const char *name, const char *bid, bool icon, bool links, const char *asar_source, const char *asar_app) {
    CFMutableDictionaryRef expected = CreatePlist(source), actual = CreatePlist(app);
    if (asar_source) {
        CFMutableDictionaryRef integrity = (CFMutableDictionaryRef)CFDictionaryGetValue(expected, CFSTR("ElectronAsarIntegrity"));
        if (!integrity || CFGetTypeID(integrity) != CFDictionaryGetTypeID() || CFDictionaryGetCount(integrity) < 1 || CFDictionaryGetCount(integrity) > 8) fail("unreviewed ElectronAsarIntegrity layout");
        // Other archives (4.29: Resources/ion-dist.asar) are copied unchanged; the
        // final CFEqual then requires their entries to match the source exactly.
        CFIndex count = CFDictionaryGetCount(integrity); const void *keys[8], *values[8]; CFDictionaryGetKeysAndValues(integrity, keys, values);
        for (CFIndex k = 0; k < count; k++) {
            if (CFGetTypeID(keys[k]) != CFStringGetTypeID() || !CFStringHasPrefix(keys[k], CFSTR("Resources/")) || !CFStringHasSuffix(keys[k], CFSTR(".asar"))) fail("unreviewed ElectronAsarIntegrity layout");
            if (CFGetTypeID(values[k]) != CFDictionaryGetTypeID() || CFDictionaryGetCount(values[k]) != 2 || !CFEqual(CFDictionaryGetValue(values[k], CFSTR("algorithm")) ?: CFSTR(""), CFSTR("SHA256"))) fail("unreviewed ElectronAsarIntegrity entry");
        }
        CFMutableDictionaryRef entry = (CFMutableDictionaryRef)CFDictionaryGetValue(integrity, CFSTR("Resources/app.asar"));
        if (!entry || CFGetTypeID(entry) != CFDictionaryGetTypeID() || CFDictionaryGetCount(entry) != 2) fail("unreviewed app.asar integrity entry");
        CFStringRef algorithm = CFDictionaryGetValue(entry, CFSTR("algorithm")), hash = CFDictionaryGetValue(entry, CFSTR("hash"));
        CFStringRef observed = CFStringCreateWithCString(NULL, asar_source, kCFStringEncodingUTF8); if (!observed) fail("invalid header digest");
        if (!algorithm || !hash || !CFEqual(algorithm, CFSTR("SHA256")) || !CFEqual(hash, observed)) fail("source ElectronAsarIntegrity does not match its archive header");
        CFRelease(observed); set_string(entry, CFSTR("hash"), asar_app);
    }
    if (!CFEqual(CFDictionaryGetValue(expected, CFSTR("CFBundleIdentifier")) ?: CFSTR(""), CFSTR("com.anthropic.claudefordesktop"))) fail("unexpected source bundle identity");
    char display[96]; int size = snprintf(display, sizeof(display), "Claude %s", name); if (size < 0 || (size_t)size >= sizeof(display)) fail("instance name too long");
    set_string(expected, CFSTR("CFBundleIdentifier"), bid); set_string(expected, CFSTR("CFBundleDisplayName"), display); set_string(expected, CFSTR("CFBundleExecutable"), "launcher");
    CFDictionaryRemoveValue(expected, CFSTR("CFBundleURLTypes")); CFDictionaryRemoveValue(expected, CFSTR("NSUserActivityTypes"));
    if (links) {
        // Exactly one URL type: "Claude <name> links" with the claude-dev scheme, nothing else.
        char label[112]; size = snprintf(label, sizeof(label), "Claude %s links", name); if (size < 0 || (size_t)size >= sizeof(label)) fail("instance name too long");
        CFMutableDictionaryRef type = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        CFMutableArrayRef schemes = CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks), types = CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks);
        if (!type || !schemes || !types) fail("allocation failed");
        set_string(type, CFSTR("CFBundleURLName"), label); CFArrayAppendValue(schemes, CFSTR("claude-dev"));
        CFDictionarySetValue(type, CFSTR("CFBundleURLSchemes"), schemes); CFArrayAppendValue(types, type);
        CFDictionarySetValue(expected, CFSTR("CFBundleURLTypes"), types); CFRelease(schemes); CFRelease(type); CFRelease(types);
    }
    if (icon) { set_string(expected, CFSTR("CFBundleIconFile"), "appicon"); CFDictionaryRemoveValue(expected, CFSTR("CFBundleIconName")); }
    if (!CFEqual(expected, actual)) fail("Info.plist differs beyond planned changes"); CFRelease(expected); CFRelease(actual);
}
static void sync_fd(int fd) { int rc; do { rc = fsync(fd); } while (rc && errno == EINTR); if (rc) syserr("record sync failed"); }

// Package product name: same-size package.json rewrite inside app.asar.
// Electron derives app.name, and so its "<name> Safe Storage" Keychain item,
// from package.json productName. "<instance> Claude" keeps the user agent
// token (spaces removed: "<instance>Claude/<version>") recognizable as the
// desktop client by claude.ai, which looks for "claude/"; "Claude <instance>"
// would yield "Claude<instance>/" and the web UI would treat it as a browser. Offsets never move: only package.json bytes
// and its integrity digests in the (equal-length) header change.
struct asar { unsigned char *bytes; size_t length, raw, payload, package, size, block, hash, blocks, block_at[64]; };
static unsigned char *read_whole(int parent, const char *path, size_t limit, size_t *length, mode_t *mode) {
    int fd = openat(parent, path, O_RDONLY | O_NOFOLLOW_ANY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) { int saved = errno; fprintf(stderr, "integrity: cannot open %s without symlinks: %s\n", path, strerror(saved)); exit(1); }
    struct stat before = info(fd);
    if (!S_ISREG(before.st_mode) || before.st_nlink != 1 || before.st_size < 16 || (uint64_t)before.st_size > limit) fail("unsupported archive/code file type/size");
    unsigned char *bytes = malloc((size_t)before.st_size); if (!bytes) syserr("allocation failed"); size_t total = 0;
    while (total < (size_t)before.st_size) {
        ssize_t n = read(fd, bytes + total, (size_t)before.st_size - total);
        if (n < 0 && errno == EINTR) continue; if (n <= 0) fail("partial archive/code file read"); total += (size_t)n;
    }
    char extra; if (read(fd, &extra, 1) != 0 || !same(before, info(fd))) fail("archive/code file changed during read");
    close(fd); *length = total; if (mode) *mode = before.st_mode & 07777; return bytes;
}
static uint32_t le32(const unsigned char *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void sha_hex(const unsigned char *bytes, size_t length, char text[65]) {
    unsigned char digest[32]; CC_SHA256_CTX c; if (!CC_SHA256_Init(&c)) fail("hash initialization failed");
    while (length) { size_t part = length > (1u << 30) ? (1u << 30) : length; update(&c, bytes, part); bytes += part; length -= part; }
    finish(&c, digest); hex(digest, text);
}
static size_t string_end(const unsigned char *p, size_t length, size_t at, bool *escaped) {
    for (size_t i = at + 1; i < length; i++) {
        if (p[i] < 0x20) fail("control byte inside JSON string");
        if (p[i] == '\\') { if (escaped) *escaped = true; if (++i >= length) break; continue; }
        if (p[i] == '"') return i + 1;
    }
    fail("unterminated JSON string"); return 0;
}
static bool space(unsigned char c) { return c == ' ' || c == '\n' || c == '\r' || c == '\t'; }
static size_t skip_space(const unsigned char *p, size_t length, size_t i) { while (i < length && space(p[i])) i++; return i; }
static bool key_is(const unsigned char *p, size_t start, size_t end, const char *key) { size_t n = strlen(key); return end - start == n + 2 && !memcmp(p + start + 1, key, n); }
static size_t digits(const unsigned char *p, size_t length, size_t *i) {
    size_t start = *i, value = 0;
    while (*i < length && p[*i] >= '0' && p[*i] <= '9') { if (*i - start >= 15) fail("ASAR number too large"); value = value * 10 + (size_t)(p[*i] - '0'); (*i)++; }
    if (*i == start || (p[start] == '0' && *i - start > 1)) fail("noncanonical ASAR number"); return value;
}
static void literal(const unsigned char *p, size_t length, size_t *i, const char *text) {
    size_t n = strlen(text); if (*i + n > length || memcmp(p + *i, text, n)) fail("unsupported ASAR package.json entry layout"); *i += n;
}
static size_t hex64(const unsigned char *p, size_t length, size_t *i) {
    size_t at = *i; if (at + 64 > length) fail("truncated ASAR digest");
    for (size_t k = 0; k < 64; k++) if (!strchr("0123456789abcdef", p[at + k]) || !p[at + k]) fail("invalid ASAR digest");
    *i += 64; return at;
}
static void parse_asar(struct asar *a) {
    const unsigned char *b = a->bytes;
    uint32_t four = le32(b), pickle = le32(b + 4), inner = le32(b + 8), raw = le32(b + 12);
    if (four != 4 || raw > 16 * 1024 * 1024 || pickle != 8 + ((raw + 3) & ~3u) || inner != pickle - 4 || 8 + (size_t)pickle > a->length) fail("unsupported ASAR pickle layout");
    for (size_t i = 16 + raw; i < 8 + (size_t)pickle; i++) if (b[i]) fail("nonzero ASAR header padding");
    a->raw = raw; a->payload = 8 + (size_t)pickle;
    const unsigned char *h = b + 16; size_t n = raw, entry = 0, found = 0, offset_at = 0;
    if (n < 10 || memcmp(h, "{\"files\":{", 10)) fail("unsupported ASAR header root");
    struct { bool has; size_t size; } stack[256]; memset(stack, 0, sizeof(stack)); int depth = 0;
    struct extent { size_t start, size, at; } *extents = NULL; size_t count = 0, capacity = 0;
    for (size_t i = 0; i < n;) {
        unsigned char c = h[i];
        if (c == '"') {
            size_t end = string_end(h, n, i, NULL), k = skip_space(h, n, end);
            if (k < n && h[k] == ':') {
                size_t v = k + 1;
                if (depth == 2 && key_is(h, i, end, "package.json")) { entry = v; found++; }
                if (key_is(h, i, end, "size") && v < n && h[v] >= '0' && h[v] <= '9') { stack[depth].size = digits(h, n, &v); stack[depth].has = true; }
                if (key_is(h, i, end, "offset") && v < n && h[v] == '"') {
                    size_t at = ++v, start = digits(h, n, &v);
                    if (v >= n || h[v] != '"' || !stack[depth].has) fail("ASAR offset without a preceding size");
                    if (count == capacity) { capacity = capacity ? capacity * 2 : 256; struct extent *grown = realloc(extents, capacity * sizeof(*extents)); if (!grown) syserr("allocation failed"); extents = grown; }
                    extents[count++] = (struct extent){start, stack[depth].size, at};
                }
            }
            i = end; continue;
        }
        if (c == '{' || c == '[') { if (++depth >= 256) fail("ASAR header too deep"); stack[depth].has = false; }
        else if (c == '}' || c == ']') { if (--depth < 0) fail("unbalanced ASAR header"); }
        i++;
    }
    if (depth != 0 || found != 1) fail("ASAR must contain exactly one top-level package.json");
    size_t i = entry, payload = a->length - a->payload;
    literal(h, n, &i, "{\"size\":"); a->size = digits(h, n, &i);
    literal(h, n, &i, ",\"offset\":\""); offset_at = i; a->package = digits(h, n, &i);
    literal(h, n, &i, "\",\"integrity\":{\"algorithm\":\"SHA256\",\"hash\":\""); a->hash = 16 + hex64(h, n, &i);
    literal(h, n, &i, "\",\"blockSize\":"); a->block = digits(h, n, &i);
    literal(h, n, &i, ",\"blocks\":[");
    for (a->blocks = 0;;) {
        if (a->blocks >= 64) fail("too many package.json integrity blocks");
        literal(h, n, &i, "\""); a->block_at[a->blocks++] = 16 + hex64(h, n, &i); literal(h, n, &i, "\"");
        if (i < n && h[i] == ',') { i++; continue; } literal(h, n, &i, "]}}"); break;
    }
    if (!a->size || !a->block || a->block > 16 * 1024 * 1024 || a->blocks != (a->size + a->block - 1) / a->block || a->package > payload || a->size > payload - a->package) fail("invalid package.json bounds or block layout");
    for (size_t k = 0; k < count; k++) {
        if (extents[k].at == offset_at || !extents[k].size) continue;
        if (extents[k].start < a->package + a->size && a->package < extents[k].start + extents[k].size) fail("package.json cannot share its payload region");
    }
    free(extents); a->package += a->payload;
}
static void check_digests(const struct asar *a, const unsigned char *data) {
    char text[65]; sha_hex(data, a->size, text); if (memcmp(a->bytes + a->hash, text, 64)) fail("package.json integrity mismatch");
    for (size_t k = 0; k < a->blocks; k++) {
        size_t start = k * a->block, size = a->size - start < a->block ? a->size - start : a->block;
        sha_hex(data + start, size, text); if (memcmp(a->bytes + a->block_at[k], text, 64)) fail("package.json block integrity mismatch");
    }
}
static size_t minify(const unsigned char *p, size_t length, unsigned char *out) {
    size_t used = 0;
    for (size_t i = 0; i < length;) {
        if (p[i] == '"') { size_t end = string_end(p, length, i, NULL); memcpy(out + used, p + i, end - i); used += end - i; i = end; }
        else { if (!space(p[i])) out[used++] = p[i]; i++; }
    }
    return used;
}
static unsigned char *rename_product(const unsigned char *old, size_t length, const char *name) {
    char value[96]; int wanted = snprintf(value, sizeof(value), "\"%s Claude\"", name);
    if (wanted < 0 || (size_t)wanted >= sizeof(value)) fail("product name too long");
    for (const char *c = name; *c; c++) if (!((*c >= 'A' && *c <= 'Z') || (*c >= 'a' && *c <= 'z') || (*c >= '0' && *c <= '9') || *c == '_' || *c == '-')) fail("product name must be a validated instance name");
    size_t value_at = 0, found = 0, raw_count = 0, delta = (size_t)wanted - 8; int depth = 0;
    for (size_t i = 0; i + 13 <= length; i++) if (!memcmp(old + i, "\"productName\"", 13)) raw_count++;
    for (size_t i = 0; i < length;) {
        unsigned char c = old[i];
        if (c == '"') {
            bool escaped = false; size_t end = string_end(old, length, i, &escaped), k = skip_space(old, length, end);
            if (k < length && old[k] == ':' && depth == 1) {
                if (escaped) fail("escaped package.json keys are unsupported");
                if (key_is(old, i, end, "productName")) { value_at = skip_space(old, length, k + 1); found++; }
            }
            i = end; continue;
        }
        if (c == '{' || c == '[') depth++; else if (c == '}' || c == ']') { if (--depth < 0) fail("unbalanced package.json"); }
        else if (!space(c) && depth == 0) fail("unexpected package.json top level");
        i++;
    }
    if (depth != 0 || found != 1 || raw_count != 1 || length < 2 || old[0] != '{') fail("package.json must have exactly one productName key");
    if (value_at + 8 > length || memcmp(old + value_at, "\"Claude\"", 8)) fail("unreviewed source productName");
    size_t after = skip_space(old, length, value_at + 8);
    if (after >= length || (old[after] != ',' && old[after] != '}')) fail("unexpected productName value");
    unsigned char *out = malloc(length); if (!out) syserr("allocation failed");
    size_t used = 0, removed = 0;
    for (size_t i = 0; i < length;) {
        if (i == value_at) { memcpy(out + used, value, (size_t)wanted); used += (size_t)wanted; i += 8; continue; }
        if (old[i] == '"') { size_t end = string_end(old, length, i, NULL); if (used + (end - i) > length) fail("not enough insignificant whitespace"); memcpy(out + used, old + i, end - i); used += end - i; i = end; continue; }
        if (space(old[i]) && removed < delta) { removed++; i++; continue; }
        if (used >= length) fail("not enough insignificant whitespace"); out[used++] = old[i++];
    }
    if (used != length || removed != delta) fail("not enough insignificant whitespace for a same-size rename");
    // Independent semantic check: whitespace-free forms differ only in the value.
    unsigned char *a = malloc(length + 64), *b = malloc(length + 64); if (!a || !b) syserr("allocation failed");
    size_t na = minify(old, length, a), nb = minify(out, length, b), at = 0, hits = 0;
    for (size_t i = 0; i + 22 <= na; i++) if (!memcmp(a + i, "\"productName\":\"Claude\"", 22)) { at = i; hits++; }
    if (hits != 1 || nb != na + delta || memcmp(b, a, at + 14) || memcmp(b + at + 14, value, (size_t)wanted) || memcmp(b + at + 14 + wanted, a + at + 22, na - at - 22)) fail("package.json changed beyond productName");
    free(a); free(b); return out;
}
// Returns the expected archive and both header digests; never trusts the copy.
static unsigned char *expected_asar(int source, const char *name, size_t *length, mode_t *mode, char before[65], char after[65]) {
    struct asar a = {0}; a.bytes = read_whole(source, ASAR, 1024ull * 1024 * 1024, &a.length, mode); parse_asar(&a);
    check_digests(&a, a.bytes + a.package); sha_hex(a.bytes + 16, a.raw, before);
    unsigned char *package = rename_product(a.bytes + a.package, a.size, name);
    memcpy(a.bytes + a.package, package, a.size); free(package);
    char text[65]; sha_hex(a.bytes + a.package, a.size, text); memcpy(a.bytes + a.hash, text, 64);
    for (size_t k = 0; k < a.blocks; k++) {
        size_t start = k * a.block, size = a.size - start < a.block ? a.size - start : a.block;
        sha_hex(a.bytes + a.package + start, size, text); memcpy(a.bytes + a.block_at[k], text, 64);
    }
    struct asar check = a; parse_asar(&check); check_digests(&check, check.bytes + check.package);
    sha_hex(a.bytes + 16, a.raw, after); *length = a.length; return a.bytes;
}
static void product_write(int app, const char *name) {
    size_t length; mode_t mode; char before[65], after[65];
    unsigned char *bytes = expected_asar(app, name, &length, &mode, before, after);
    if ((mode & 0022) || !(mode & 0400)) fail("unexpected app.asar mode");
    int fd = openat(app, ASAR_TEMP, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600); if (fd < 0) syserr("cannot create exclusive staged archive");
    size_t written = 0;
    while (written < length) {
        ssize_t n = write(fd, bytes + written, length - written > (1u << 30) ? (1u << 30) : length - written);
        if (n < 0 && errno == EINTR) continue; if (n <= 0) { int saved = errno; unlinkat(app, ASAR_TEMP, 0); errno = saved; syserr("staged archive write failed"); }
        written += (size_t)n;
    }
    if (fchmod(fd, mode)) { unlinkat(app, ASAR_TEMP, 0); syserr("cannot set staged archive mode"); }
    sync_fd(fd); if (close(fd)) { unlinkat(app, ASAR_TEMP, 0); syserr("staged archive close failed"); }
    if (renameat(app, ASAR_TEMP, app, ASAR)) { int saved = errno; unlinkat(app, ASAR_TEMP, 0); errno = saved; syserr("cannot replace staged archive"); }
    int resources = openat(app, "Contents/Resources", O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC); if (resources < 0) syserr("cannot open resources");
    sync_fd(resources); close(resources); free(bytes); printf("%s\n", after);
}
static void product_check(int source, int app, const char *name, char before[65], char after[65]) {
    size_t length, actual_length; unsigned char *expected = expected_asar(source, name, &length, NULL, before, after);
    unsigned char *actual = read_whole(app, ASAR, 1024ull * 1024 * 1024, &actual_length, NULL);
    if (actual_length != length || memcmp(actual, expected, length)) fail("app.asar differs beyond the planned productName change");
    free(expected); free(actual);
}

// Thin 64-bit Mach-O as codesign leaves it: the signature is the last thing in
// the file, and re-signing changes only it plus two size fields that describe it.
struct macho { size_t signature, signature_size_at, linkedit_at, slot; bool has_slot; };
static uint64_t le64(const unsigned char *p) { return (uint64_t)le32(p) | (uint64_t)le32(p + 4) << 32; }
static struct macho parse_macho(const unsigned char *b, size_t length) {
    struct macho m = {0}; size_t linkedit = 0, signature = 0;
    if (length < 32 || le32(b) != 0xfeedfacf) fail("unreviewed Mach-O layout (thin 64-bit only)");
    uint32_t ncmds = le32(b + 16), sizeofcmds = le32(b + 20); size_t at = 32, end = 32 + (size_t)sizeofcmds;
    if (end > length) fail("truncated Mach-O load commands");
    for (uint32_t i = 0; i < ncmds; i++) {
        if (at + 8 > end) fail("truncated Mach-O load command");
        uint32_t cmd = le32(b + at), size = le32(b + at + 4); if (size < 8 || at + size > end) fail("invalid Mach-O load command size");
        if (cmd == 0x19) { // LC_SEGMENT_64
            if (size < 72 || 72 + (size_t)le32(b + at + 64) * 80 != size) fail("invalid Mach-O segment");
            if (!memcmp(b + at + 8, "__LINKEDIT\0", 11)) { if (linkedit) fail("duplicate __LINKEDIT"); linkedit = at; }
            for (size_t k = 0; k < le32(b + at + 64); k++) {
                const unsigned char *section = b + at + 72 + k * 80;
                if (strncmp((const char *)section, "__asar_integrity", 16) || strncmp((const char *)section + 16, "__DATA_CONST", 16)) continue;
                if (m.has_slot) fail("duplicate integrity digest section");
                uint64_t bytes = le64(section + 40); uint32_t offset = le32(section + 48);
                if (bytes < 66 || offset + bytes > length) fail("invalid integrity digest section");
                m.slot = offset; m.has_slot = true;
            }
        } else if (cmd == 0x1d) { // LC_CODE_SIGNATURE
            if (size != 16 || signature) fail("invalid Mach-O signature command"); signature = at;
        }
        at += size;
    }
    if (at != end || !linkedit || !signature) fail("unreviewed Mach-O load commands");
    m.signature = le32(b + signature + 8); m.signature_size_at = signature + 12; m.linkedit_at = linkedit;
    if (m.signature < end || m.signature > length) fail("invalid Mach-O signature offset");
    if (m.has_slot && (m.slot + 66 > m.signature || memcmp(b + m.slot, DIGEST_SENTINEL, 32))) fail("unexpected integrity digest slot");
    return m;
}
// The copy must equal the source except its own signature and, if given, the digest.
static void code_check(const unsigned char *source, size_t source_length, const unsigned char *app, size_t app_length, const unsigned char digest[32]) {
    struct macho s = parse_macho(source, source_length), a = parse_macho(app, app_length);
    if (s.signature != a.signature || s.linkedit_at != a.linkedit_at || s.has_slot != a.has_slot || s.slot != a.slot || (digest && !s.has_slot)) fail("re-signed code layout moved");
    unsigned char *expected = malloc(s.signature); if (!expected) syserr("allocation failed");
    memcpy(expected, source, s.signature);
    memcpy(expected + s.linkedit_at + 32, app + a.linkedit_at + 32, 8); memcpy(expected + s.linkedit_at + 48, app + a.linkedit_at + 48, 8);
    memcpy(expected + s.signature_size_at, app + a.signature_size_at, 4);
    if (digest) memcpy(expected + s.slot + 34, digest, 32);
    if (memcmp(expected, app, s.signature)) fail("re-signed code differs beyond its signature and integrity digest");
    free(expected);
}
static void string_bytes(CC_SHA256_CTX *c, CFTypeRef value) {
    char text[1024];
    if (!value || CFGetTypeID(value) != CFStringGetTypeID() || !CFStringGetCString(value, text, sizeof(text), kCFStringEncodingUTF8)) fail("invalid ElectronAsarIntegrity string");
    update(c, text, strlen(text));
}
static CFComparisonResult literal_order(const void *a, const void *b, void *unused) { (void)unused; return CFStringCompare(a, b, 0); }
// Same bytes as Electron's IsIntegrityDictionaryValid: sorted path, algorithm, hash.
static void dictionary_digest(int app, unsigned char digest[32]) {
    CFMutableDictionaryRef info = CreatePlist(app); CFDictionaryRef integrity = CFDictionaryGetValue(info, CFSTR("ElectronAsarIntegrity"));
    if (!integrity || CFGetTypeID(integrity) != CFDictionaryGetTypeID()) fail("missing ElectronAsarIntegrity");
    CFIndex count = CFDictionaryGetCount(integrity); if (count < 1 || count > 8) fail("unreviewed ElectronAsarIntegrity layout");
    const void *keys[8]; CFDictionaryGetKeysAndValues(integrity, keys, NULL);
    for (CFIndex k = 0; k < count; k++) if (CFGetTypeID(keys[k]) != CFStringGetTypeID()) fail("unreviewed ElectronAsarIntegrity layout");
    CFArrayRef sorted = CFArrayCreate(NULL, keys, count, &kCFTypeArrayCallBacks); CFMutableArrayRef order = CFArrayCreateMutableCopy(NULL, count, sorted);
    if (!sorted || !order) fail("allocation failed");
    CFArraySortValues(order, CFRangeMake(0, count), literal_order, NULL);
    CC_SHA256_CTX c; if (!CC_SHA256_Init(&c)) fail("hash initialization failed");
    for (CFIndex k = 0; k < count; k++) {
        CFStringRef key = CFArrayGetValueAtIndex(order, k); CFDictionaryRef entry = CFDictionaryGetValue(integrity, key);
        if (!entry || CFGetTypeID(entry) != CFDictionaryGetTypeID()) fail("unreviewed ElectronAsarIntegrity entry");
        string_bytes(&c, key); string_bytes(&c, CFDictionaryGetValue(entry, CFSTR("algorithm"))); string_bytes(&c, CFDictionaryGetValue(entry, CFSTR("hash")));
    }
    finish(&c, digest); CFRelease(order); CFRelease(sorted); CFRelease(info);
}
// Slot state of the framework: false when absent or unused (Electron fails open).
static bool slot_used(const unsigned char *bytes, size_t length, struct macho *m) {
    *m = parse_macho(bytes, length); if (!m->has_slot || !bytes[m->slot + 32]) return false;
    if (bytes[m->slot + 32] != 1 || bytes[m->slot + 33] != 1) fail("unreviewed integrity digest slot version"); return true;
}
static void seal_write(int app) {
    struct stat named;
    if (fstatat(app, FRAMEWORK, &named, AT_SYMLINK_NOFOLLOW)) { if (errno == ENOENT) { printf("none\n"); return; } syserr("cannot inspect framework"); }
    size_t length; unsigned char *bytes = read_whole(app, FRAMEWORK, 2048ull * 1024 * 1024, &length, NULL); struct macho m;
    if (!slot_used(bytes, length, &m)) { free(bytes); printf("none\n"); return; }
    unsigned char digest[32]; dictionary_digest(app, digest);
    int fd = openat(app, FRAMEWORK, O_WRONLY | O_NOFOLLOW_ANY | O_CLOEXEC); if (fd < 0) syserr("cannot open framework for its digest");
    struct stat s = info(fd); if (!S_ISREG(s.st_mode) || s.st_nlink != 1 || s.st_uid != getuid() || (size_t)s.st_size != length) fail("framework changed before its digest write");
    if (pwrite(fd, digest, 32, (off_t)(m.slot + 34)) != 32) syserr("framework digest write failed");
    sync_fd(fd); if (close(fd)) syserr("framework close failed"); free(bytes); printf("sealed\n");
}
// Returns whether the framework and helpers were re-signed; checks each of them.
static bool sealed_code(int source, int app) {
    struct stat named;
    if (fstatat(source, FRAMEWORK, &named, AT_SYMLINK_NOFOLLOW)) { if (errno == ENOENT) return false; syserr("cannot inspect source framework"); }
    size_t source_length, app_length; struct macho m;
    unsigned char *original = read_whole(source, FRAMEWORK, 2048ull * 1024 * 1024, &source_length, NULL);
    if (!slot_used(original, source_length, &m)) { free(original); return false; }
    unsigned char digest[32]; dictionary_digest(app, digest);
    unsigned char *copy = read_whole(app, FRAMEWORK, 2048ull * 1024 * 1024, &app_length, NULL);
    code_check(original, source_length, copy, app_length, digest); free(original); free(copy);
    int frameworks = openat(source, "Contents/Frameworks", O_RDONLY | O_DIRECTORY | O_NOFOLLOW_ANY | O_CLOEXEC); if (frameworks < 0) syserr("cannot open source frameworks");
    size_t count; char **items = names(frameworks, &count); close(frameworks);
    for (size_t i = 0; i < count; i++) {
        size_t length = strlen(items[i]); char path[PATH_MAX];
        if (length > 4 && !strcmp(items[i] + length - 4, ".app")) {
            int size = snprintf(path, sizeof(path), "Contents/Frameworks/%s/Contents/MacOS/%.*s", items[i], (int)(length - 4), items[i]);
            if (size < 0 || (size_t)size >= sizeof(path)) fail("helper path too long");
            if (!fstatat(source, path, &named, AT_SYMLINK_NOFOLLOW)) {
                original = read_whole(source, path, 2048ull * 1024 * 1024, &source_length, NULL); copy = read_whole(app, path, 2048ull * 1024 * 1024, &app_length, NULL);
                code_check(original, source_length, copy, app_length, NULL); free(original); free(copy);
            } else if (errno != ENOENT) syserr("cannot inspect helper");
        }
        free(items[i]);
    }
    free(items); return true;
}

static void record_create(int records, const char *id, const char *binding, struct hashes source, struct hashes app) {
    if (mkdirat(records, id, 0700)) syserr("record UUID already exists or cannot be created");
    int directory = openat(records, id, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC); if (directory < 0) syserr("cannot open record directory");
    char source_hex[65], fixed_hex[65], app_hex[65], buffer[400]; hex(source.full, source_hex); hex(source.unchanged, fixed_hex); hex(app.full, app_hex);
    int length = snprintf(buffer, sizeof(buffer), "MCD_INTEGRITY 1\nconfig %s\nsource %s\nunchanged %s\nbundle %s\n", binding, source_hex, fixed_hex, app_hex);
    if (length <= 0 || (size_t)length >= sizeof(buffer)) fail("record serialization failed");
    int fd = openat(directory, "record.txt", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600); if (fd < 0) syserr("cannot create exclusive build record");
    size_t written = 0; while (written < (size_t)length) { ssize_t n = write(fd, buffer + written, (size_t)length - written); if (n < 0 && errno == EINTR) continue; if (n <= 0) syserr("record write failed"); written += (size_t)n; }
    sync_fd(fd); if (close(fd)) syserr("record close failed"); sync_fd(directory); sync_fd(records); close(directory);
}
static void field(const char **cursor, const char *end, const char *label, char value[65]) {
    size_t length = strlen(label);
    if ((size_t)(end - *cursor) < length + 65 || memcmp(*cursor, label, length)) fail("partial/noncanonical integrity record");
    *cursor += length; memcpy(value, *cursor, 64); value[64] = '\0'; *cursor += 64;
    if (!digest(value) || *(*cursor)++ != '\n') fail("invalid integrity digest");
}
int main(int argc, char **argv) {
    if (!getuid() || getuid() != geteuid()) fail("requires an unprivileged user"); umask(077);
    if (argc == 3 && !strcmp(argv[1], "seal")) {
        // Staging-only writer, after the Info.plist hash; the record re-derives it.
        int app = root(argv[2], false, false); char id[37]; marker(app, id); seal_write(app); return 0;
    }
    if (argc == 4 && !strcmp(argv[1], "product-name")) {
        // Staging-only writer; record later regenerates and compares from source.
        int app = root(argv[2], false, false); char id[37]; marker(app, id);
        struct stat unused; if (!fstatat(app, ASAR_TEMP, &unused, AT_SYMLINK_NOFOLLOW) || errno != ENOENT) fail("stale staged archive present");
        product_write(app, argv[3]); return 0;
    }
    bool create = argc == 12 && !strcmp(argv[1], "record"), verify = argc == 5 && !strcmp(argv[1], "verify");
    if (!create && !verify) fail("usage: integrity record ROOT UUID APP SOURCE NAME ID CONFIG ICON PRODUCT LINKS | verify ROOT APP CONFIG | product-name APP NAME | seal APP");
    const char *app_path = create ? argv[4] : argv[3], *binding = create ? argv[8] : argv[4];
    if (!digest(binding) || (create && (!uuid(argv[3]) || (strcmp(argv[9], "0") && strcmp(argv[9], "1")) || (strcmp(argv[10], "0") && strcmp(argv[10], "1")) || (strcmp(argv[11], "0") && strcmp(argv[11], "1"))))) fail("invalid UUID/config/icon/product/links arguments");
    int records = root(argv[2], false, true), app = root(app_path, false, false); char id[37]; marker(app, id);
    if (create) {
        if (strcmp(id, argv[3])) fail("build marker UUID mismatch");
        int source = root(argv[5], true, false); bool icon = !strcmp(argv[9], "1"), product = !strcmp(argv[10], "1");
        struct stat unused;
        if (!fstatat(source, MARKER, &unused, AT_SYMLINK_NOFOLLOW) || errno != ENOENT) fail("source contains a reserved build marker");
        if (!fstatat(source, "Contents/MacOS/launcher", &unused, AT_SYMLINK_NOFOLLOW) || errno != ENOENT) fail("source contains a reserved launcher");
        char asar_before[65], asar_after[65];
        if (product) product_check(source, app, argv[6], asar_before, asar_after);
        else { struct stat unused; if (!fstatat(app, ASAR_TEMP, &unused, AT_SYMLINK_NOFOLLOW) || errno != ENOENT) fail("unexpected staged archive"); }
        planned_metadata(source, app, argv[6], argv[7], icon, !strcmp(argv[11], "1"), product ? asar_before : NULL, product ? asar_after : NULL);
        bool code = product && sealed_code(source, app);
        struct hashes original = tree(source, argv[5], true, icon, product, code), generated = tree(app, app_path, false, icon, product, code);
        if (memcmp(original.unchanged, generated.unchanged, 32)) fail("app resources differ beyond planned files");
        record_create(records, id, binding, original, generated); printf("RECORDED %s\n", id); return 0;
    }
    int directory = openat(records, id, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC); if (directory < 0) syserr("missing integrity record; rebuild required");
    struct stat s = info(directory); if (s.st_uid != getuid() || (s.st_mode & 07777) != 0700) fail("unsafe record directory"); acl_check(directory);
    char buffer[400]; size_t length = read_small(directory, "record.txt", buffer, sizeof(buffer) - 1);
    if (length < 16 || memcmp(buffer, "MCD_INTEGRITY 1\n", 16)) fail("invalid integrity record header");
    const char *cursor = buffer + 16, *end = buffer + length; char recorded[65], source[65], fixed[65], expected[65];
    field(&cursor, end, "config ", recorded); field(&cursor, end, "source ", source); field(&cursor, end, "unchanged ", fixed); field(&cursor, end, "bundle ", expected);
    if (cursor != end || strcmp(recorded, binding)) fail("extra bytes or different instance/config binding");
    struct hashes observed = tree(app, app_path, false, false, false, false); char current[65]; hex(observed.full, current);
    if (strcmp(current, expected)) fail("bundle content/structure/mode drift; local re-signing does not repair the build record");
    printf("VERIFIED %s (local content record; not an authenticated trust boundary)\n", id); return 0;
}
