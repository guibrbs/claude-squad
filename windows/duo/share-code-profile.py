#!/usr/bin/env python3
"""Compartilha com o Duo arquivos escolhidos do Claude Code original.

Liga (destino -> origem): CLAUDE.md, skills, output-styles, tools, plugins e cada
projects/<projeto>/memory. Pastas viram junction (não precisa de admin nem do Modo
de Desenvolvedor); um arquivo vira link simbólico quando o Windows permite, senão
hardlink. O settings.json é copiado uma vez, sem as chaves que escolheriam outra
conta ou provedor. Nunca toca login, sessões, histórico, transcrições, caches ou
estado da conta: só os nomes da lista abaixo são lidos ou escritos.

Com --mcp-servers, os servidores MCP de escopo de usuário (mcpServers do
~/.claude.json do original) são copiados para o .claude.json do Duo, um nome por
vez: um nome que o Duo já tem é mantido, e nada mais em nenhum dos arquivos é
alterado. Depois de escolhido, as próximas execuções também copiam os servidores
novos do original.

O padrão é simulação. --apply faz as mudanças com o Claude Duo fechado; um item
que já existe no destino é movido, nunca apagado, para uma pasta de backup, e só
com --backup-existing.

Rode com `py -3 -I`. Veja docs/PROFILE_REUSE.md.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

try:
    import _winapi
except ImportError:  # not Windows: tests patch the link helpers
    _winapi = None


def _load_config():
    spec = importlib.util.spec_from_file_location("duo_config", Path(__file__).resolve().parent / "duo_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


duo_config = _load_config()

LINKED = ("CLAUDE.md", "skills", "output-styles", "tools", "plugins")
MANIFEST = ".shared-from-original.json"
# Keys that choose a different login, provider or credential source.
ACCOUNT_KEYS = {"apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh",
                "forceLoginMethod", "forceLoginOrgUUID", "otelHeadersHelper"}
ACCOUNT_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH", "AWS_", "GOOGLE_",
                        "CLOUD_ML_", "VERTEX_", "CLAUDE_CONFIG_DIR")
MERGED_FROM_DESTINATION = ("extraKnownMarketplaces",)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class Refused(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("chave JSON repetida no settings.json: " + key)
        result[key] = value
    return result


def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def is_link(path):
    """A symbolic link or a junction (both are reparse points this script may have made)."""
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def link_target(path):
    target = os.readlink(path)
    return target[4:] if target.startswith("\\\\?\\") else target


def same_place(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def real_dir(path, label):
    if not os.path.isdir(path) or is_link(path):
        raise Refused(f"{label} precisa ser uma pasta de verdade (não um link): {path}")


def safe_source(path):
    """A source item must be a real file or directory, never a link: links are not chained."""
    if is_link(path):
        raise Refused(f"a origem é um link; não encadeio links: {path}")
    info = os.lstat(path)
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise Refused(f"a origem não é arquivo nem pasta comum: {path}")


def make_link(source, destination):
    """Junction for a folder; for a file, a symbolic link when allowed, otherwise a hardlink."""
    if os.path.isdir(source):
        _winapi.CreateJunction(str(source), str(destination))
        return "junction"
    try:
        os.symlink(str(source), str(destination))
        return "symlink"
    except OSError:
        os.link(str(source), str(destination))  # same volume only; an editor that replaces the file breaks it
        return "hardlink"


def plan_link(source, destination):
    if not os.path.lexists(destination):
        return "link", None
    if is_link(destination):
        if same_place(link_target(destination), source):
            return "ok", None
        return "conflict", "link para outro lugar"
    if os.path.isfile(destination) and os.path.isfile(source) and os.path.samefile(destination, source):
        return "ok", None  # hardlink made by an earlier run
    if os.path.isdir(destination) and not any(os.scandir(destination)):
        return "replace-empty", None
    return "conflict", "item que já existe no Duo"


def filtered_settings(source, destination):
    original = json.loads(read_text(source), object_pairs_hook=unique_object)
    if not isinstance(original, dict):
        raise Refused("o settings.json precisa ser um objeto JSON")
    removed = sorted(k for k in original if k in ACCOUNT_KEYS)
    result = {k: v for k, v in original.items() if k not in ACCOUNT_KEYS}
    env = result.get("env")
    if env is not None:
        if not isinstance(env, dict):
            raise Refused("o env do settings.json precisa ser um objeto")
        dropped = sorted(k for k in env if k.startswith(ACCOUNT_ENV_PREFIXES))
        removed += ["env." + k for k in dropped]
        result["env"] = {k: v for k, v in env.items() if k not in dropped}
    if destination.exists():
        current = json.loads(read_text(destination), object_pairs_hook=unique_object)
        if not isinstance(current, dict):
            raise Refused("o settings.json do Duo precisa ser um objeto JSON")
        for key in MERGED_FROM_DESTINATION:
            if isinstance(current.get(key), dict):
                merged = dict(current[key])
                merged.update(result.get(key) or {})
                result[key] = merged
    return result, removed


def read_state(path, label):
    state = json.loads(read_text(path), object_pairs_hook=unique_object)
    if not isinstance(state, dict):
        raise Refused(f"{label} precisa ser um objeto JSON")
    servers = state.get("mcpServers", {})
    if not isinstance(servers, dict) or not all(isinstance(v, dict) for v in servers.values()):
        raise Refused(f"o mcpServers de {label} precisa ser um objeto de objetos")
    return state, servers


def plan_mcp_servers(source_state, destination_state):
    """Names to copy and names kept as they are in the Duo; values are never printed."""
    safe_source(source_state)
    _, original = read_state(source_state, "o .claude.json do original")
    if is_link(destination_state):
        raise Refused("o .claude.json do Duo é um link; revise antes")
    current = read_state(destination_state, "o .claude.json do Duo")[1] if destination_state.exists() else {}
    copy = sorted(name for name in original if name not in current)
    kept = sorted(name for name in original if name in current and current[name] != original[name])
    return original, copy, kept


def transport(server):
    return server.get("type") or ("http" if "url" in server else "stdio")


def write_json(path, temporary, data):
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv=None, *, duo_running=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=home / ".claude")
    parser.add_argument("--destination", type=Path, default=duo_config.code_config_dir())
    parser.add_argument("--apply", action="store_true", help="faz as mudanças (padrão: simulação)")
    parser.add_argument("--backup-existing", action="store_true",
                        help="move para um backup os itens que já existem no Duo, em vez de recusar")
    parser.add_argument("--source-state", type=Path, default=home / ".claude.json",
                        help="o .claude.json do original, lido só com --mcp-servers")
    parser.add_argument("--mcp-servers", action="store_true",
                        help="copia também os servidores MCP de escopo de usuário (mantido nas próximas execuções)")
    args = parser.parse_args(argv)
    duo_running = duo_running or duo_config.duo_running
    try:
        source, destination = args.source.absolute(), args.destination.absolute()
        real_dir(source, "a origem")
        real_dir(destination, "o destino")
        real_source, real_destination = os.path.realpath(source).lower(), os.path.realpath(destination).lower()
        if (real_source == real_destination or real_destination.startswith(real_source + os.sep)
                or real_source.startswith(real_destination + os.sep)):
            raise Refused("origem e destino precisam ser pastas separadas")
        if args.apply and duo_running():
            raise Refused("o Claude Duo está aberto; feche-o (bandeja > Sair) e rode de novo")

        actions = []  # (kind, source, destination, note)
        for name in LINKED:
            item = source / name
            if not os.path.lexists(item):
                actions.append(("skip", item, destination / name, "não existe no original"))
                continue
            safe_source(item)
            kind, note = plan_link(item, destination / name)
            actions.append((kind, item, destination / name, note))
        projects = source / "projects"
        if os.path.lexists(projects):
            safe_source(projects)
            for entry in sorted(os.scandir(projects), key=lambda e: e.name):
                memory = Path(entry.path) / "memory"
                if not entry.is_dir(follow_symlinks=False) or not os.path.lexists(memory):
                    continue
                safe_source(memory)
                target = destination / "projects" / entry.name / "memory"
                kind, note = plan_link(memory, target)
                actions.append((kind, memory, target, note))
        duo_projects = destination / "projects"
        duo_only = []
        if duo_projects.is_dir() and not is_link(duo_projects):
            for entry in os.scandir(duo_projects):
                memory = Path(entry.path) / "memory"
                if memory.is_dir() and not is_link(memory) and not (projects / entry.name / "memory").exists():
                    duo_only.append(memory)

        settings_source, settings_destination = source / "settings.json", destination / "settings.json"
        settings, removed, settings_kind = None, [], "skip"
        manifest = destination / MANIFEST
        previous = json.loads(read_text(manifest)) if manifest.exists() else {}
        if settings_source.exists():
            safe_source(settings_source)
            if is_link(settings_destination):
                raise Refused("o settings.json do Duo é um link; revise antes")
            settings, removed = filtered_settings(settings_source, settings_destination)
            if previous.get("settings_copied"):
                settings_kind = "ok"  # Copied once; later edits in the Duo are kept.
            elif settings_destination.exists() and json.loads(read_text(settings_destination)) == settings:
                settings_kind = "ok"
            else:
                settings_kind = "copy"

        share_mcp = args.mcp_servers or "mcp_servers" in previous
        state_destination = destination / ".claude.json"
        servers, mcp_copy, mcp_kept = {}, [], []
        if share_mcp:
            if not args.source_state.exists():
                raise Refused(f".claude.json do original não encontrado: {args.source_state}")
            servers, mcp_copy, mcp_kept = plan_mcp_servers(args.source_state.absolute(), state_destination)

        conflicts = [a for a in actions if a[0] == "conflict"]
        for kind, src, dst, note in actions:
            print(f"{kind:14} {dst} -> {src}" + (f"  ({note})" if note else ""))
        print(f"{settings_kind:14} {settings_destination} (cópia única do original)")
        for key in removed:
            print(f"{'não copiado':14} settings.json {key} (escolhe uma conta ou provedor)")
        for memory in duo_only:
            print(f"{'mantido':14} {memory} (memória só do Duo; não é mesclada)")
        for name in mcp_copy:
            print(f"{'copiar mcp':14} {name} ({transport(servers[name])})")
        for name in mcp_kept:
            print(f"{'mantido mcp':14} {name} (o Duo tem a própria definição)")
        if mcp_copy:
            print("Os servidores MCP levam comando, argumentos, env e headers como estão, credenciais incluídas.")
        if not args.apply:
            if conflicts and not args.backup_existing:
                print("Os itens do Duo acima seriam movidos para um backup: acrescente --backup-existing.")
            print("Simulação: nada mudou. Use --apply com o Claude Duo fechado.")
            return 0
        if conflicts and not args.backup_existing:
            raise Refused("os itens do Duo acima seriam substituídos; rode de novo com --backup-existing para "
                          "movê-los para um backup")

        backup = destination / (".share-backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))

        def move_aside(path):
            target = backup / path.relative_to(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, target)

        made = {}
        for kind, src, dst, _ in actions:
            if kind in ("skip", "ok"):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if kind == "replace-empty":
                os.rmdir(dst)
            elif kind == "conflict":
                move_aside(dst)
            made[str(dst)] = make_link(src, dst)
        if settings_kind == "copy":
            if settings_destination.exists():
                move_aside(settings_destination)
            write_json(settings_destination, destination / ".settings.json.share-new", settings)
        if mcp_copy:
            # Reread right before writing so only mcpServers changes in the Duo's state.
            state, current = (read_state(state_destination, "o .claude.json do Duo")
                              if state_destination.exists() else ({}, {}))
            state["mcpServers"] = {**current, **{name: servers[name] for name in mcp_copy if name not in current}}
            write_json(state_destination, destination / ".claude.json.share-new", state)
        record = {
            "source": str(source),
            "links": sorted(str(a[2].relative_to(destination)) for a in actions if a[0] != "skip"),
            "settings_copied": settings_kind in ("copy", "ok"),
            "settings_sha256": (hashlib.sha256(settings_destination.read_bytes()).hexdigest()
                                if settings_destination.exists() else None),
            "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if share_mcp:
            record["mcp_servers"] = sorted(set(previous.get("mcp_servers", [])) | set(mcp_copy))
        temporary = destination / (MANIFEST + ".new")
        temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest)
        for path, how in made.items():
            if how == "hardlink":
                print(f"hardlink: {path} (o Windows não permitiu link simbólico; ative o Modo de Desenvolvedor "
                      "e rode de novo se o editor substituir o arquivo)")
        if backup.exists():
            print(f"Itens anteriores do Duo movidos para {backup}")
        print("Aplicado. Login, sessões, histórico e transcrições não foram tocados." +
              (" Abra uma nova sessão do Code no Duo para carregar os servidores MCP." if mcp_copy else ""))
        return 0
    except Refused as error:
        print("recusado: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
