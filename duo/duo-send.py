#!/usr/bin/env python3
"""duo-send: the original Claude Code hands a task to the Duo's account.

"duo" is the CLAUDE_CONFIG_DIR of the Duo line in the installed config (default
~/.claude-duo), the same account loop-duo uses. `--to NAME` picks another
managed copy of the config instead (`--to trio`); every command takes it.
Two ways to send:

- synchronous (default): `claude -p` on the Duo's account, in the repository;
  waits and prints the answer, like a subagent on the other account.
- --bg: `claude --bg` on the Duo's account; returns at once with the id of a
  background session that `duo-send.py agents` lists and `attach` opens.

`sessions` lists the sessions open on the Duo's account with the address their
`SendMessage` socket answers on: the original's sessions cannot list the Duo's,
but they can send to that address, and the reply comes back by itself.

`open` (and `send --open`) shows a session in the Claude Duo window through a
claude-dev:// link, which the copy registers when the config's `@links
claude-dev` line names it (or names nothing, with one copy): a new Code session with the folder and the prompt filled
in, or a session duo-send ran, imported by its id.

The account, its environment and the `claude -p` turn come from loop-duo.py,
so both scripts isolate the Duo the same way. Nothing here touches the apps.

Run with /usr/bin/python3 -I. See docs/DUO_SEND.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import urllib.parse

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
OPEN = "/usr/bin/open"
SCHEME = "claude-dev"
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
PROMPT_LIMIT = 4000  # A link is visible in process listings; long briefs go through `send`.
# What the Duo's claude answers when its own setup is missing (see "Before the first send"): a turn the sign-in
# check let through, or --bg in a folder the Duo's account never trusted. Only claude's own wording: a bare
# "not logged in" also matches gh's and npm's errors, which a failed turn may quote.
SIGNED_OUT = re.compile(r"please run /login", re.IGNORECASE)
UNTRUSTED = re.compile(r"workspace not trusted", re.IGNORECASE)

ROLE_SYNC = """\
You are running on the Claude {name} account, one of the Claude Code accounts of this Mac. The original account
sent you this task with duo-send and is waiting for your final answer, which goes back to it as a tool result.
- Nobody can answer you during this turn: there is no AskUserQuestion here. When the task needs a decision only
  a person can make (access, credentials, spending, something outside the repository), stop and say exactly
  what is missing in your final answer instead of guessing.
- Do not merge, push to the default branch or change settings and permissions.
- End with a short report: what you did, files touched, how you checked it and what is left.
"""

ROLE_BG = """\
You are running on the Claude {name} account, one of the Claude Code accounts of this Mac. The original account
started you as a background session with duo-send. The user follows this session with `claude agents` and can
attach to it to answer questions.
- Do not merge, push to the default branch or change settings and permissions.
- End with a short report: what you did, files touched, how you checked it and what is left.
"""


SOCKET = re.compile(r"^/(?:private/)?tmp/cc-socks/\d{1,10}\.sock$")


def is_socket(path):
    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except OSError:
        return False


def open_duo_sessions(match=None, *, include_stale=False, to="duo"):
    """The copy's sessions from its registry (<config>/sessions/<pid>.json), with their SendMessage address.

    The address is the session's messaging socket; it changes when the session's
    process restarts, so it is read right before sending, never kept.
    """
    folder = duo(to)["folder"] / "sessions"
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.is_symlink():
            continue
        data = loop.read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
            continue
        name, socket = str(data.get("name") or ""), str(data.get("messagingSocketPath") or "")
        if match and match.lower() not in name.lower():
            continue
        live = loop.process_alive(data["pid"]) and bool(SOCKET.match(socket)) and is_socket(socket)
        if not live and not include_stale:
            continue
        found.append({"name": name, "status": data.get("status"), "kind": data.get("kind"), "pid": data["pid"],
                      "cwd": data.get("cwd"), "session": data.get("sessionId"), "live": live,
                      "address": f"uds:{socket}" if live else None})
    return found


def duo(to="duo"):
    """The copy's account ({id, name, folder}); duo-send never targets the original."""
    if str(to).lower() == "original":
        raise Refused("duo-send hands work from the original to a copy; --to names a copy (duo, trio...)")
    acct = loop.account(to)
    if not acct["folder"].is_dir():
        raise Refused(f"the {acct['name']} account has no Code folder yet ({acct['folder']}); "
                      f"open Code in Claude {acct['name']} once")
    return acct


def duo_app(to="duo"):
    """The copy's bundle id; the config's `@links claude-dev` line must give the links to that copy."""
    config = loop.installed_config()
    if not config:
        raise Refused("Claude Squad is not installed (no config); run duo/install-duo.sh")
    acct, found = loop.account(to), loop.copies()
    links = None
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("@links"):
            links = line.split(None, 1)[1].strip() if len(line.split(None, 1)) == 2 else ""
    scheme, _, target = (links or "").partition(" ")
    if scheme != SCHEME:
        raise Refused(f"the {acct['name']} does not take links yet ({config} has no `@links {SCHEME}`): run "
                      "duo/install-duo.sh --links from an up-to-date checkout (see docs/DUO_VISIBLE_SESSION.md)")
    target = target.strip().lower() or (next(iter(found)) if len(found) == 1 else "")
    if target != acct["id"]:
        owner = f"Claude {found[target]['name']}" if target in found else "another copy"
        raise Refused(f"Claude {acct['name']} does not take links: the config gives claude-dev:// to {owner} "
                      f"(`@links {SCHEME} <name>` in {config}), and only one app can have it")
    bundle = found[acct["id"]]["bundle"]
    if not bundle or not BUNDLE_ID.match(bundle):
        raise Refused(f"no valid {acct['name']} line in {config}")
    return bundle


def open_link(path, params, to="duo"):
    """Hands a claude-dev:// link to the copy's app by bundle id; returns the link."""
    bundle = duo_app(to)
    link = f"{SCHEME}://{path}?" + urllib.parse.urlencode([*params, ("source", "duo-send")], quote_via=urllib.parse.quote)
    done = subprocess.run([OPEN, "-b", bundle, link], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    if done.returncode != 0:
        raise Refused(f"open did not reach Claude {loop.account(to)['name']}: {(done.stderr or done.stdout).strip()[:300]}")
    return link


def open_session(*, folder=None, prompt=None, session=None, to="duo"):
    """A new Code session in the Duo window (folder, prompt filled in), or a CLI session imported by id."""
    if session is not None:
        if folder or prompt:
            raise Refused("--session opens an existing session; leave out --repo and --prompt")
        if not UUID.match(session):
            raise Refused(f"invalid session id (the Duo imports a full UUID): {session!r}")
        return {"ok": True, "link": open_link("resume", [("session", session)], to)}
    if prompt and len(prompt) > PROMPT_LIMIT:
        raise Refused(f"the prompt is longer than {PROMPT_LIMIT} characters; use `send` and then `open --session`")
    params = [("folder", str(repository(folder)))]
    if prompt and prompt.strip():
        params.append(("q", prompt))
    return {"ok": True, "link": open_link("code/new", params, to)}


def repository(folder):
    path = Path(folder).expanduser() if folder else Path.cwd()
    if not path.is_dir():
        raise Refused(f"the folder {path} does not exist")
    return path.resolve()


def check_options(task, *, model, name, session, cost_max):
    if not task.strip():
        raise Refused("the task is empty")
    if model and not loop.MODEL.match(model):
        raise Refused(f"invalid model: {model!r}")
    if name and not NAME.match(name):
        raise Refused(f"invalid name: {name!r}")
    if session is not None and not loop.SESSION_ID.match(session):
        raise Refused(f"invalid session id: {session!r}")
    if cost_max is not None and cost_max <= 0:
        raise Refused("--max-cost must be above zero")


def follow_up(acct, command=None, target=None):
    """The command line that reaches the Duo's account from the user's terminal."""
    prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(str(acct['folder']))} claude" + (f" {command}" if command else "")
    return f"{prefix} {shlex.quote(target)}" if target else prefix


def hint(acct, where, error):
    """The one-time fix, as commands for the user's Terminal, when the copy's claude refused for its setup."""
    name = acct["name"]
    if SIGNED_OUT.search(error or ""):
        return (f"the {name} account's claude command is not signed in (the Claude {name} app signs in only its own "
                f"sessions). Once, in a Terminal outside any Claude session: `{loop.login_command(acct)}` with the "
                f"{name} account, then check with `{follow_up(acct, 'auth status')}`")
    if UNTRUSTED.search(error or ""):
        return (f"the {name} account does not trust {where} yet. Once, in a Terminal outside any Claude session: "
                f"`cd {shlex.quote(str(where))} && {follow_up(acct)}`, accept the trust prompt and /exit; "
                f"or open the folder in Claude {name}'s Code tab")
    return None


def send_sync(task, *, folder=None, model=None, permission="auto", cost_max=None, session=None, say=print, to="duo"):
    """One `claude -p` turn on the copy's account; returns {ok, session, cost_usd, answer, error, hint}."""
    check_options(task, model=model, name=None, session=session, cost_max=cost_max)
    acct, where = duo(to), repository(folder)
    loop.require_signed_in(acct)
    extra = ["--max-budget-usd", f"{cost_max:.2f}"] if cost_max is not None else []
    end = loop.converse(acct, session, task, folder=where, role_prompt=ROLE_SYNC.format(name=acct["name"]),
                        reviewer=False, model=model, permission=permission, extra=extra,
                        on_tool=lambda tool, data: say(f"  · {acct['id']} {tool}: {loop.tool_summary(tool, data)}"))
    return {"mode": "sync", "account": acct["id"], "repo": str(where), **end,
            "hint": None if end["ok"] else hint(acct, where, end["error"])}


def send_background(task, *, folder=None, model=None, permission="auto", name=None, session=None, to="duo"):
    """Starts a `claude --bg` session on the copy's account; returns its id and the follow-up commands."""
    check_options(task, model=model, name=name, session=session, cost_max=None)
    acct, where = duo(to), repository(folder)
    loop.require_signed_in(acct)
    argv = [loop.claude_bin(), "--bg", *(["--resume", session] if session else []),
            *(["--name", name] if name else []), *(["--model", model] if model else []),
            "--permission-mode", permission, "--append-system-prompt", ROLE_BG.format(name=acct["name"]), task]
    started = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, env=loop.environment(acct), cwd=where, timeout=120)
    output = (started.stdout or "") + (started.stderr or "")
    found = BACKGROUND_ID.search(output)
    if started.returncode != 0 or not found:
        error = output.strip()[-400:] or f"exited with code {started.returncode}"
        return {"mode": "bg", "account": acct["id"], "repo": str(where), "ok": False, "id": None,
                "error": error, "hint": hint(acct, where, output or error)}
    agent_id = found.group(1)
    return {"mode": "bg", "account": acct["id"], "repo": str(where), "ok": True, "id": agent_id, "error": None,
            "hint": None,
            "commands": {verb: follow_up(acct, verb, None if verb == "agents" else agent_id) for verb in FOLLOW_UP}}


def run_follow_up(verb, target=None, *, all_sessions=False, to="duo"):
    """`claude agents|logs|stop|attach` on the copy's account: the original's would list its own sessions."""
    acct = duo(to)
    argv = [loop.claude_bin(), verb]
    if verb == "agents":
        argv += ["--json", *(["--all"] if all_sessions else [])]  # the plain list needs a terminal
    else:
        if not target or not NAME.match(target):
            raise Refused(f"invalid session id or name: {target!r}")
        argv.append(target)
    if verb == "attach":
        os.execve(argv[0], argv, loop.environment(acct))  # interactive: hands the terminal over
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, env=loop.environment(acct), timeout=60)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def main(argv=None):
    if os.getuid() == 0:
        print("duo-send: run as your own user, not as root", file=sys.stderr)
        return 1
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="duo-send", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("--to", default="duo", metavar="NAME",
                        help="the copy whose account this reaches: duo (default), trio or another in the config")
    send = commands.add_parser("send", parents=[target], help="hands a task to the Duo's account")
    send.add_argument("task", help='the task; "-" reads it from standard input')
    send.add_argument("--repo", help="the folder the Duo works in (default: the current folder)")
    send.add_argument("--bg", action="store_true", help="starts a background session and returns its id")
    send.add_argument("--resume", metavar="SESSION", help="continues a Duo session instead of starting one")
    send.add_argument("--name", help="display name of the background session (--bg)")
    send.add_argument("--model")
    send.add_argument("--permission", choices=PERMISSION_MODES, default="auto")
    send.add_argument("--max-cost", type=float, help="spending ceiling in US$ (synchronous mode only)")
    send.add_argument("--open", action="store_true", help="afterwards, opens the session in the Claude Duo window")
    send.add_argument("--json", action="store_true")
    opening = commands.add_parser("open", parents=[target], help="opens a session in the Claude Duo window (needs @links claude-dev)")
    opening.add_argument("--repo", help="folder of the new session (default: the current folder)")
    opening.add_argument("--prompt", help='text filled in, not sent; "-" reads it from standard input')
    opening.add_argument("--session", help="imports this session id (a UUID) instead of starting a new one")
    sessions = commands.add_parser("sessions", parents=[target], help="lists the Duo's open sessions with their SendMessage address")
    sessions.add_argument("match", nargs="?", help="only sessions whose name contains this text")
    sessions.add_argument("--address", action="store_true",
                          help="prints only the address; exactly one session must match")
    sessions.add_argument("--all", action="store_true", help="also sessions whose process or socket is gone")
    sessions.add_argument("--json", action="store_true")
    agents = commands.add_parser("agents", parents=[target], help="lists the Duo's background sessions (JSON)")
    agents.add_argument("--all", action="store_true", help="also the finished ones")
    for verb, text in (("logs", "prints a Duo background session's recent output"),
                       ("stop", "stops a Duo background session"),
                       ("attach", "opens a Duo background session in this terminal")):
        commands.add_parser(verb, parents=[target], help=text).add_argument("session", help="the id (or part of the name)")
    args = parser.parse_args(argv)

    to = args.to
    try:
        label = duo(to)["name"] if args.command != "open" else loop.account(to)["name"]
        if args.command == "open":
            prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
            result = open_session(folder=args.repo, prompt=prompt, session=args.session, to=to)
            print(f"opened in Claude {label}: {result['link'].split('?')[0]}")
            return 0
        if args.command == "sessions":
            found = open_duo_sessions(args.match, include_stale=args.all and not args.address, to=to)
            if args.address:
                if len(found) != 1:
                    raise Refused((f"{len(found)} open {label} sessions match {args.match!r}" if args.match else
                                   "--address needs a name to match") + (": " + "; ".join(f["name"] for f in found)
                                                                          if found else ""))
                print(found[0]["address"])
            elif args.json:
                print(json.dumps({"sessions": found}, ensure_ascii=False, indent=2))
            else:
                for item in found:
                    print(f"{item['status'] or '?':<6} {item['address'] or '(gone)':<28} {item['name']}")
                if not found:
                    print(f"No open {label} sessions" + (f" match {args.match!r}." if args.match else "."))
            return 0
        if args.command != "send":
            return run_follow_up(args.command, getattr(args, "session", None), all_sessions=getattr(args, "all", False), to=to)
        task = sys.stdin.read() if args.task == "-" else args.task
        if args.open:
            duo_app(to)  # refuses before any work when the copy takes no links
        if args.bg:
            if args.open:
                raise Refused("--open needs the session to finish first; with --bg, open it later with "
                              "`duo-send.py open --session <uuid>`")
            if args.max_cost is not None:
                raise Refused("--max-cost works only in the synchronous mode: claude enforces it with -p alone")
            result = send_background(task, folder=args.repo, model=args.model, permission=args.permission,
                                     name=args.name, session=args.resume, to=to)
        else:
            if args.name:
                raise Refused("--name names a background session; use it with --bg")
            say = (lambda text: None) if args.json else (lambda text: print(text, file=sys.stderr))
            result = send_sync(task, folder=args.repo, model=args.model, permission=args.permission,
                               cost_max=args.max_cost, session=args.resume, say=say, to=to)
            if args.open and result["ok"] and result.get("session"):
                try:
                    result["opened"] = open_session(session=result["session"], to=to)["link"]
                except Refused as error:
                    result["open_error"] = str(error)
    except Refused as error:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"duo-send: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("duo-send: claude did not answer in time", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result["ok"]:
        print(f"duo-send: the {label} account did not answer: {result['error']}", file=sys.stderr)
        if result.get("hint"):
            print(f"duo-send: {result['hint']}", file=sys.stderr)
    elif result["mode"] == "bg":
        print(f"started on the {label} account · {result['id']} · {result['repo']}")
        for verb in FOLLOW_UP:
            print(f"  {result['commands'][verb]}")
    else:
        print(result["answer"])
        if result.get("opened") or result.get("open_error"):
            print(f"[{result['account']} · {f'opened in Claude {label}' if result.get('opened') else result['open_error']}]", file=sys.stderr)
        print(f"\n[{result['account']} · session {result['session']} · US$ {result['cost_usd'] or 0:.2f} estimated]", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
