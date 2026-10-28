#!/usr/bin/env python3
"""duo-send: o Claude Code original passa uma tarefa para a conta do Duo.

"duo" é o codeConfigDir do duo.json instalado (padrão ~/.claude-duo), a mesma
conta que o loop-duo usa. Dois jeitos de enviar:

- síncrono (padrão): `claude -p` na conta do Duo, no repositório; espera e
  imprime a resposta, como um subagente na outra conta.
- --bg: `claude --bg` na conta do Duo; volta na hora com o id de uma sessão em
  segundo plano que `duo-send.py agents` lista e `attach` abre.

`sessions` lista as sessões abertas na conta do Duo com o endereço do
`SendMessage` delas (um named pipe): as sessões do original não enxergam as do
Duo, mas conseguem enviar para esse endereço, e a resposta volta sozinha.

A conta, o ambiente e o turno `claude -p` vêm do loop-duo.py, então os dois
scripts isolam o Duo do mesmo jeito. Nada aqui mexe nos apps.

Rode com `py -3 -I`. Veja docs/DUO_SEND.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def _load_loop_duo():
    """loop-duo.py has a hyphen in its name, so it is loaded from its path (also under -I)."""
    spec = importlib.util.spec_from_file_location("loop_duo", HERE / "loop-duo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loop = _load_loop_duo()
Refused = loop.Refused

# bypassPermissions is left out, as in loop-duo: nobody watches a sent task.
PERMISSION_MODES = loop.PERMISSION_MODES
NAME = re.compile(r"^[\w .:/-]{1,60}$")
BACKGROUND_ID = re.compile(r"backgrounded\s*\S*\s*([0-9A-Za-z][0-9A-Za-z-]{5,80})")
FOLLOW_UP = ("agents", "logs", "attach", "stop")
# Windows sessions answer SendMessage on a named pipe (macOS uses a Unix socket).
PIPE = re.compile(r"^\\\\\.\\pipe\\(?:LOCAL\\)?cc-msg-[0-9A-Za-z-]{8,80}$")

ROLE_SYNC = """\
You are running on the Claude Duo account, the second Claude Code account of this Windows PC. The original
account sent you this task with duo-send and is waiting for your final answer, which goes back to it as a tool
result.
- Nobody can answer you during this turn: there is no AskUserQuestion here. When the task needs a decision only
  a person can make (access, credentials, spending, something outside the repository), stop and say exactly
  what is missing in your final answer instead of guessing.
- Do not merge, push to the default branch or change settings and permissions.
- End with a short report: what you did, files touched, how you checked it and what is left.
"""

ROLE_BG = """\
You are running on the Claude Duo account, the second Claude Code account of this Windows PC. The original
account started you as a background session with duo-send. The user follows this session with
`claude-duo agents` and can attach to it to answer questions.
- Do not merge, push to the default branch or change settings and permissions.
- End with a short report: what you did, files touched, how you checked it and what is left.
"""


def open_duo_sessions(match=None, *, include_stale=False):
    """The Duo's sessions from its registry (<config>/sessions/<pid>.json), with their SendMessage address.

    The address is the session's messaging pipe; it changes when the session's
    process restarts, so it is read right before sending, never kept.
    """
    folder = duo()["folder"] / "sessions"
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.is_symlink():
            continue
        data = loop.read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
            continue
        name, pipe = str(data.get("name") or ""), str(data.get("messagingSocketPath") or "")
        if match and match.lower() not in name.lower():
            continue
        live = loop.process_alive(data["pid"]) and bool(PIPE.match(pipe))
        if not live and not include_stale:
            continue
        found.append({"name": name, "status": data.get("status"), "kind": data.get("kind"), "pid": data["pid"],
                      "cwd": data.get("cwd"), "session": data.get("sessionId"), "live": live,
                      "address": f"uds:{pipe}" if live else None})
    return found


def duo():
    acct = loop.account("duo")
    if not acct["folder"].is_dir():
        raise Refused(f"a conta do Duo ainda não tem pasta do Code ({acct['folder']}); rode scripts\\install.ps1 "
                      "e abra o Code no Claude Duo uma vez")
    return acct


def repository(folder):
    path = Path(folder).expanduser() if folder else Path.cwd()
    if not path.is_dir():
        raise Refused(f"a pasta {path} não existe")
    return path.resolve()


def check_options(task, *, model, name, session, cost_max):
    if not task.strip():
        raise Refused("a tarefa está vazia")
    if model and not loop.MODEL.match(model):
        raise Refused(f"modelo inválido: {model!r}")
    if name and not NAME.match(name):
        raise Refused(f"nome inválido: {name!r}")
    if session is not None and not loop.SESSION_ID.match(session):
        raise Refused(f"id de sessão inválido: {session!r}")
    if cost_max is not None and cost_max <= 0:
        raise Refused("--max-cost precisa ser maior que zero")


def follow_up(command, target=None):
    """The command line that reaches the Duo's session list from the user's terminal."""
    return f'claude-duo {command} "{target}"' if target else f"claude-duo {command}"


def send_sync(task, *, folder=None, model=None, permission="auto", cost_max=None, session=None, say=print):
    """One `claude -p` turn on the Duo's account; returns {ok, session, cost_usd, answer, error}."""
    check_options(task, model=model, name=None, session=session, cost_max=cost_max)
    acct, where = duo(), repository(folder)
    loop.require_signed_in(acct)
    extra = ["--max-budget-usd", f"{cost_max:.2f}"] if cost_max is not None else []
    end = loop.converse(acct, session, task, folder=where, role_prompt=ROLE_SYNC, reviewer=False, model=model,
                        permission=permission, extra=extra,
                        on_tool=lambda tool, data: say(f"  · duo {tool}: {loop.tool_summary(tool, data)}"))
    return {"mode": "sync", "account": "duo", "repo": str(where), **end}


def send_background(task, *, folder=None, model=None, permission="auto", name=None, session=None):
    """Starts a `claude --bg` session on the Duo's account; returns its id and the follow-up commands."""
    check_options(task, model=model, name=name, session=session, cost_max=None)
    acct, where = duo(), repository(folder)
    loop.require_signed_in(acct)
    argv = [loop.claude_bin(), "--bg", *(["--resume", session] if session else []),
            *(["--name", name] if name else []), *(["--model", model] if model else []),
            "--permission-mode", permission, "--append-system-prompt", ROLE_BG, task]
    started = subprocess.run(argv, stdin=subprocess.DEVNULL, env=loop.environment(acct), cwd=where, timeout=120,
                             **loop.TEXT)
    output = (started.stdout or "") + (started.stderr or "")
    found = BACKGROUND_ID.search(output)
    if started.returncode != 0 or not found:
        return {"mode": "bg", "account": "duo", "repo": str(where), "ok": False, "id": None,
                "error": output.strip()[-400:] or f"saiu com o código {started.returncode}"}
    agent_id = found.group(1)
    return {"mode": "bg", "account": "duo", "repo": str(where), "ok": True, "id": agent_id, "error": None,
            "commands": {verb: follow_up(verb, None if verb == "agents" else agent_id) for verb in FOLLOW_UP}}


def run_follow_up(verb, target=None, *, all_sessions=False):
    """`claude agents|logs|stop|attach` on the Duo's account: the original's would list its own sessions."""
    acct = duo()
    argv = [loop.claude_bin(), verb]
    if verb == "agents":
        argv += ["--json", *(["--all"] if all_sessions else [])]  # the plain list needs a terminal
    else:
        if not target or not NAME.match(target):
            raise Refused(f"id ou nome de sessão inválido: {target!r}")
        argv.append(target)
    if verb == "attach":
        # Interactive: the child gets this console; Windows has no exec that replaces the process.
        return subprocess.run(argv, env=loop.environment(acct)).returncode
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, env=loop.environment(acct), timeout=60, **loop.TEXT)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="duo-send", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send", help="passa uma tarefa para a conta do Duo")
    send.add_argument("task", help='a tarefa; "-" lê da entrada padrão')
    send.add_argument("--repo", help="a pasta em que o Duo trabalha (padrão: a pasta atual)")
    send.add_argument("--bg", action="store_true", help="abre uma sessão em segundo plano e devolve o id")
    send.add_argument("--resume", metavar="SESSION", help="continua uma sessão do Duo em vez de abrir outra")
    send.add_argument("--name", help="nome da sessão em segundo plano (--bg)")
    send.add_argument("--model")
    send.add_argument("--permission", choices=PERMISSION_MODES, default="auto")
    send.add_argument("--max-cost", type=float, help="teto de gasto em US$ (só no modo síncrono)")
    send.add_argument("--json", action="store_true")
    sessions = commands.add_parser("sessions", help="lista as sessões abertas do Duo com o endereço do SendMessage")
    sessions.add_argument("match", nargs="?", help="só as sessões cujo nome contém este texto")
    sessions.add_argument("--address", action="store_true",
                          help="imprime só o endereço; exatamente uma sessão precisa casar")
    sessions.add_argument("--all", action="store_true", help="inclui as sessões cujo processo já acabou")
    sessions.add_argument("--json", action="store_true")
    agents = commands.add_parser("agents", help="lista as sessões em segundo plano do Duo (JSON)")
    agents.add_argument("--all", action="store_true", help="inclui as encerradas")
    for verb, text in (("logs", "imprime a saída recente de uma sessão em segundo plano do Duo"),
                       ("stop", "encerra uma sessão em segundo plano do Duo"),
                       ("attach", "abre uma sessão em segundo plano do Duo neste terminal")):
        commands.add_parser(verb, help=text).add_argument("session", help="o id (ou parte do nome)")
    args = parser.parse_args(argv)

    try:
        if args.command == "sessions":
            found = open_duo_sessions(args.match, include_stale=args.all and not args.address)
            if args.address:
                if len(found) != 1:
                    raise Refused((f"{len(found)} sessões abertas do Duo casam com {args.match!r}" if args.match else
                                   "--address precisa de um nome para casar") + (": " + "; ".join(f["name"] for f in found)
                                                                                 if found else ""))
                print(found[0]["address"])
            elif args.json:
                print(json.dumps({"sessions": found}, ensure_ascii=False, indent=2))
            else:
                for item in found:
                    print(f"{item['status'] or '?':<6} {item['address'] or '(encerrada)':<60} {item['name']}")
                if not found:
                    print("Nenhuma sessão aberta do Duo" + (f" casa com {args.match!r}." if args.match else "."))
            return 0
        if args.command != "send":
            return run_follow_up(args.command, getattr(args, "session", None), all_sessions=getattr(args, "all", False))
        task = sys.stdin.read() if args.task == "-" else args.task
        if args.bg:
            if args.max_cost is not None:
                raise Refused("--max-cost só vale no modo síncrono: o claude só aplica o teto com -p")
            result = send_background(task, folder=args.repo, model=args.model, permission=args.permission,
                                     name=args.name, session=args.resume)
        else:
            if args.name:
                raise Refused("--name dá nome a uma sessão em segundo plano; use junto com --bg")
            say = (lambda text: None) if args.json else (lambda text: print(text, file=sys.stderr))
            result = send_sync(task, folder=args.repo, model=args.model, permission=args.permission,
                               cost_max=args.max_cost, session=args.resume, say=say)
    except Refused as error:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"duo-send: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("duo-send: o claude não respondeu a tempo", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result["ok"]:
        print(f"duo-send: a conta do Duo não respondeu: {result['error']}", file=sys.stderr)
    elif result["mode"] == "bg":
        print(f"iniciada na conta do Duo · {result['id']} · {result['repo']}")
        for verb in FOLLOW_UP:
            print(f"  {result['commands'][verb]}")
    else:
        print(result["answer"])
        print(f"\n[duo · sessão {result['session']} · US$ {result['cost_usd'] or 0:.2f} estimado]", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
