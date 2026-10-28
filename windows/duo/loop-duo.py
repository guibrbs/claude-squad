#!/usr/bin/env python3
"""loop-duo: as duas contas do Claude Code deste Windows trabalham num mesmo repositório.

Uma conta desenvolve e a outra revisa, rodada após rodada, até o revisor aprovar
o objetivo. "original" é ~/.claude; "duo" é o codeConfigDir do duo.json instalado
(padrão ~/.claude-duo). Toda resposta termina com um bloco ```loop-duo em JSON
que diz a este script o que vem depois. `claude -p` não tem AskUserQuestion, então
uma pergunta que um agente faria ao usuário vira um bloco "question", respondido
pelo outro agente.

O trabalho vai para um branch loop-duo/<objetivo>-<id> criado a partir do HEAD,
por padrão numa worktree própria. O revisor roda sem as ferramentas de edição. No
máximo, o loop faz push do branch e abre um PR em rascunho depois da aprovação: o
merge fica com uma pessoa. O estado fica em %LOCALAPPDATA%\\claude-duo\\loops\\<id>\\.

Rode com `py -3 -I`. Veja docs/LOOP_DUO.md.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata


def _load_config():
    spec = importlib.util.spec_from_file_location("duo_config", Path(__file__).resolve().parent / "duo_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


duo_config = _load_config()
Refused = duo_config.Refused
process_alive = duo_config.process_alive

HOME = Path.home()
LOOPS = duo_config.DATA / "loops"
ACCOUNTS = ("original", "duo")
LEVELS = ("local", "pr")  # No merge level on purpose: a person merges.
# bypassPermissions is left out: the loop runs unattended.
PERMISSION_MODES = ("auto", "acceptEdits", "manual", "dontAsk", "plan")
NO_EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")
QUESTIONS_PER_ROUND = 4
BLOCK = re.compile(r"```loop-duo[ \t]*\r?\n(.*?)```", re.S)
LOOP_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
SESSION_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{6,80}$")
MODEL = re.compile(r"^[A-Za-z0-9.\-\[\]_]{2,60}$")
ROLE_NAMES = {"dev": "desenvolvedor", "reviewer": "revisor"}
# Git and gh never read the console: a hook or prompt waiting on it would hang the loop.
TEXT = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}

ROLE_DEV = """\
You are the DEVELOPER in a loop-duo: two Claude Code agents, on different accounts, working on the same
repository. You implement; the other agent, the REVIEWER, reviews every round and answers your questions
during the loop.

- When you need a decision you would ask the user for (including a skill's questionnaire), ask the
  reviewer with the "question" state and end your answer there.
- Work only in the current folder: it is the loop's branch. Commit what you do, with clear messages; the
  reviewer sees your work through git. Do not push, open a PR or merge.
- Run the project's tests before handing in the round, when there are any.
- If a tool is denied by permissions, do not insist: take another path, ask the reviewer or close as
  "blocked".
- End EVERY answer with exactly one block fenced by ```loop-duo and ```, holding valid JSON:

```loop-duo
{"state": "review", "summary": "what changed in this round and how you tested it"}
```

Possible states:
- "review": the round is ready for review (with "summary").
- "question": you need a decision ("questions": [{"question": "...", "options": ["...", "..."]}];
  "options" is optional).
- "answer": the reviewer asked you questions and you are answering ("answers": ["..."], in the same order).
- "blocked": you cannot go on even with the reviewer's help ("reason": what is missing, such as an access).
"""

ROLE_REVIEWER = """\
You are the REVIEWER in a loop-duo: two Claude Code agents, on different accounts, working on the same
repository. The other agent, the DEVELOPER, implements; you review every round against the goal and answer
the developer's questions during the loop.

- Do not edit files or commit. Review by reading the code and running git diff, git log and the project's
  tests. A turn in which HEAD or a tracked file changes stops the loop, and its verdict does not count.
- Point out what keeps the goal from being met, with file:line. Personal preference does not hold back
  approval: there are few rounds.
- When the developer asks, decide as a tech lead would, by the goal, the code and the repository's
  conventions. When there are options, pick one. What only a person can decide (access, credentials,
  spending, something outside the repository), do not decide: answer that it must close as "blocked".
- Approve only when the goal is fully met and the tests pass.
- End EVERY answer with exactly one block fenced by ```loop-duo and ```, holding valid JSON:

```loop-duo
{"state": "changes", "comments": ["file:line — what to fix and why"]}
```

Possible states:
- "approved": the goal is met ("summary": the final verdict, in a few lines).
- "changes": something still needs fixing ("comments": one item per fix).
- "question": you need an explanation from the developer before deciding ("questions": [{"question": "..."}]).
- "answer": you are answering the developer's questions ("answers": ["..."], in the same order).
"""

ASK_FOR_BLOCK = ("Your answer did not end with the agreed ```loop-duo block. Answer again with only the block, "
                 "holding valid JSON, in the format of your instructions.")


class Stop(Exception):
    """The loop stops without an engine error: a ceiling, a block or an account failure."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def now():
    return datetime.datetime.now().astimezone()


def iso(moment):
    return moment.isoformat(timespec="seconds")


def write_private(path, data):
    temporary = path.with_name(path.name + ".tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- accounts


def account(name):
    folder = HOME / ".claude" if name == "original" else duo_config.code_config_dir()
    return {"id": name, "folder": folder}


def environment(acct):
    """This PC's environment without what another Claude session left in it.

    A Desktop session exports its own account (CLAUDE_CODE_* and the like), which
    the other account must not inherit. The original account runs without
    CLAUDE_CONFIG_DIR: pointed at ~/.claude, it would move ~/.claude.json into it.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("CLAUDE", "ANTHROPIC_"))}
    if acct["id"] != "original":
        env["CLAUDE_CONFIG_DIR"] = str(acct["folder"])
    return env


def claude_bin():
    local = HOME / ".local/bin/claude.exe"
    found = shutil.which("claude") or (str(local) if local.exists() else None)
    if not found:
        raise Refused("o comando claude não está no PATH")
    return found


def git_bin():
    found = shutil.which("git")
    if not found:
        raise Refused("o comando git não está no PATH")
    return found


def login_command(acct):
    """What the user types in a terminal to sign this account's Claude Code CLI in."""
    return "claude auth login" if acct["id"] == "original" else "claude-duo auth login"


def require_signed_in(acct):
    """Refuses when `claude auth status` says the account's CLI is signed out.

    The Desktop app keeps its own sign-in: a Duo that works in its window can
    still have a CLI that never signed in. An unreadable answer lets the run go
    on, and a real auth error then shows up in the turn itself.
    """
    try:
        done = subprocess.run([claude_bin(), "auth", "status"], timeout=30, stdin=subprocess.DEVNULL,
                              env=environment(acct), **TEXT)
        status = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return
    if isinstance(status, dict) and status.get("loggedIn") is False:
        raise Refused(f"o Claude Code CLI da conta {acct['id']} não está logado; num terminal fora do Claude, "
                      f"rode  {login_command(acct)}  e entre com essa conta")


def gh_bin():
    found = shutil.which("gh")
    if not found:
        raise Refused("o comando gh, que abre o PR, não foi encontrado")
    return found


def converse(acct, session, text, *, folder, role_prompt, reviewer, model, permission, on_tool, extra=()):
    """One turn of `acct` in `folder`: continues `session` (or starts one) and returns its result.

    `extra` goes before the output flags; duo-send uses it for --max-budget-usd.
    """
    if session is not None and not SESSION_ID.match(session):
        raise Refused(f"id de sessão inválido: {session!r}")
    argv = [claude_bin(), "-p", *(["--resume", session] if session else []), *(["--model", model] if model else []),
            "--permission-mode", permission, "--append-system-prompt", role_prompt,
            *(["--disallowedTools", *NO_EDIT_TOOLS] if reviewer else []), *extra,
            "--output-format", "stream-json", "--verbose"]
    errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8")  # a file: a full stderr pipe would block
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
                               encoding="utf-8", errors="replace", env=environment(acct), cwd=folder)
    process.stdin.write(text)
    process.stdin.close()
    result = None
    for line in process.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            session = event.get("session_id") or session
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    on_tool(str(block.get("name")), block.get("input") or {})
        elif event.get("type") == "result":
            result = event
    code = process.wait()
    errors.seek(0)
    errors = errors.read()
    ok = code == 0 and result is not None and not result.get("is_error")
    return {"ok": ok, "session": (result or {}).get("session_id") or session,
            "cost_usd": (result or {}).get("total_cost_usd") or 0,
            "answer": (result or {}).get("result") if ok else None,
            "error": None if ok else ((result or {}).get("result") or errors.strip()[-400:] or f"saiu com o código {code}")}


# ---------------------------------------------------------------- the protocol


def loop_block(text):
    """The last ```loop-duo block of an answer; None if absent, not JSON or without a state."""
    found = BLOCK.findall(text or "")
    if not found:
        return None
    try:
        data = json.loads(found[-1])
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("state"), str) else None


def without_block(text):
    return BLOCK.sub("", text or "").strip()


def texts(value):
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value] if isinstance(value, list) else []


def questions_of(block):
    """The questions as [{question, options}], also accepting a list of strings."""
    out = []
    for item in block.get("questions") or []:
        if isinstance(item, str) and item.strip():
            out.append({"question": item.strip(), "options": []})
        elif isinstance(item, dict) and str(item.get("question", "")).strip():
            out.append({"question": str(item["question"]).strip(), "options": texts(item.get("options"))})
    return out


def quote_questions(questions):
    lines = []
    for number, item in enumerate(questions, start=1):
        options = f"  (options: {' | '.join(item['options'])})" if item["options"] else ""
        lines.append(f"{number}. {item['question']}{options}")
    return "\n".join(lines)


# ---------------------------------------------------------------- git


def git(folder, *args, check=True):
    process = subprocess.run([git_bin(), "-C", str(folder), *args], stdin=subprocess.DEVNULL, **TEXT)
    if check and process.returncode != 0:
        raise Refused(f"git {' '.join(args)}: {(process.stderr or process.stdout).strip()[:300]}")
    return process.stdout.strip()


def slug(text, limit=32):
    """The goal as part of a branch name: ascii, lower case, hyphens."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:limit].rstrip("-") or "task"


def prepare_area(folder, loop_id, goal, worktree):
    """The loop branch from the current HEAD: in its own worktree (default) or in the folder itself."""
    origin = Path(folder).expanduser() if folder else Path.cwd()
    if not origin.is_dir():
        raise Refused(f"a pasta {origin} não existe")
    if git(origin, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise Refused(f"{origin} não é um repositório git; o loop-duo trabalha num branch")
    root = Path(git(origin, "rev-parse", "--show-toplevel"))
    base = git(root, "rev-parse", "HEAD")
    source_branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = f"loop-duo/{slug(goal)}-{loop_id[-4:]}"
    if worktree:
        tree = LOOPS / loop_id / "tree"
        git(root, "worktree", "add", "-b", branch, str(tree), base)
    else:
        if git(root, "status", "--porcelain"):
            raise Refused("a pasta tem mudanças sem commit; faça o commit antes ou deixe o loop usar uma worktree "
                          "(o padrão)")
        git(root, "switch", "-c", branch)
        tree = root
    return {"root": str(root), "tree": str(tree), "branch": branch, "base": base,
            "from": None if source_branch == "HEAD" else source_branch, "worktree": worktree}


def untracked_files(area, limit=50):
    """Untracked paths in the worktree that no .gitignore covers (a whole untracked folder as one line)."""
    listed = git(area["tree"], "ls-files", "--others", "--exclude-standard", "--directory", check=False)
    return listed.splitlines()[:limit]


def tree_state(area):
    """HEAD, the branch and the tracked changes of the worktree, to tell whether a turn changed them.

    Untracked files are left out: a reviewer running the tests writes caches, and
    the loop never commits untracked files anyway.
    """
    tree = area["tree"]
    tracked = subprocess.run([git_bin(), "-C", tree, "diff", "HEAD", "--binary"], capture_output=True,
                             stdin=subprocess.DEVNULL).stdout
    return {"head": git(tree, "rev-parse", "HEAD", check=False),
            "branch": git(tree, "rev-parse", "--abbrev-ref", "HEAD", check=False),
            "tracked": hashlib.sha256(tracked).hexdigest(),
            "files": git(tree, "diff", "HEAD", "--name-only", check=False).splitlines()}


def tree_changes(before, after):
    """What changed between two tree_state() snapshots, in words; empty when nothing did."""
    changes = []
    if (before["head"], before["branch"]) != (after["head"], after["branch"]):
        changes.append(f"o HEAD foi de {before['branch']} {before['head'][:10]} para {after['branch']} {after['head'][:10]}")
    if before["tracked"] != after["tracked"]:
        files = sorted(set(before["files"]) | set(after["files"])) or ["(arquivos versionados)"]
        changes.append("arquivos versionados mudaram: " + ", ".join(files[:20]))
    return "; ".join(changes)


def commit_leftovers(area, round_number):
    """Commits the tracked changes the developer left uncommitted, so the reviewer sees a closed round.

    Untracked files are never added: a .env, a log or a dump a test left behind
    would otherwise be committed and, with --until pr, pushed.
    """
    if not git(area["tree"], "status", "--porcelain", "--untracked-files=no"):
        return False
    git(area["tree"], "add", "-u")
    done = subprocess.run([git_bin(), "-C", area["tree"], "commit", "-q", "-m",
                           f"loop-duo: round {round_number} (left uncommitted)"], stdin=subprocess.DEVNULL, **TEXT)
    return done.returncode == 0


def open_pr(state, summary, verdict):
    """Pushes the loop branch and opens a draft PR; never merges."""
    area = state["area"]
    git(area["tree"], "push", "-u", "origin", area["branch"])
    title = state["goal"].strip().splitlines()[0][:72]
    body = (f"## Objetivo\n\n{state['goal'].strip()}\n\n## O que foi feito\n\n{summary.strip()}\n\n"
            f"## Revisão ({state['reviewer']})\n\n{verdict.strip()}\n\n"
            f"Feito pelo loop-duo do Claude Duo: a conta {state['dev']} desenvolveu e a conta {state['reviewer']} "
            f"revisou, em {state['round']} rodada(s). Uma pessoa ainda precisa revisar antes do merge.\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
    args = [gh_bin(), "pr", "create", "--draft", "--head", area["branch"], "--title", title, "--body", body]
    if area.get("from"):
        args += ["--base", area["from"]]
    created = subprocess.run(args, cwd=area["tree"], stdin=subprocess.DEVNULL, **TEXT)
    if created.returncode != 0:
        raise Refused(f"o gh não abriu o PR: {(created.stderr or created.stdout).strip()[:300]}")
    return created.stdout.strip().splitlines()[-1] if created.stdout.strip() else None


# ---------------------------------------------------------------- the loop


def run_loop(goal, *, dev="original", reviewer="duo", folder=None, worktree=True, rounds=6, until="local",
             cost_max=None, model_dev=None, model_reviewer=None, permission="auto", say=print):
    if dev == reviewer:
        raise Refused("o desenvolvedor e o revisor precisam ser contas diferentes")
    accounts = {"dev": account(dev), "reviewer": account(reviewer)}
    for acct in accounts.values():
        if not acct["folder"].is_dir():
            raise Refused(f"a conta {acct['id']} ainda não tem pasta do Code ({acct['folder']}); "
                          "abra o Code com essa conta uma vez")
    if not goal.strip():
        raise Refused("o objetivo está vazio")
    for acct in accounts.values():
        require_signed_in(acct)
    for model in (model_dev, model_reviewer):
        if model and not MODEL.match(model):
            raise Refused(f"modelo inválido: {model!r}")
    if rounds < 1:
        raise Refused("o loop precisa de pelo menos uma rodada")
    loop_id = f"{now():%Y%m%d-%H%M%S}-{os.urandom(2).hex()}"
    home = LOOPS / loop_id
    home.mkdir(mode=0o700, parents=True)
    try:
        area = prepare_area(folder, loop_id, goal, worktree)
    except Refused:
        home.rmdir()  # still empty: nothing ran
        raise
    transcript = home / "conversation.md"
    models = {"dev": model_dev, "reviewer": model_reviewer}
    state = {"id": loop_id, "dev": dev, "reviewer": reviewer, "goal": goal, "until": until, "area": area,
             "round": 0, "max_rounds": rounds, "sessions": {"dev": None, "reviewer": None}, "cost_usd": 0.0,
             "status": "running", "started": iso(now()), "conversation": str(transcript), "pid": os.getpid()}

    def save():
        write_private(home / "loop.json", state)

    def write(title, text):
        with open(os.open(transcript, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as file:
            file.write(f"\n## {title}\n\n{text.strip()}\n")

    def speak(role, text):
        """One turn of `role`: sends the text and returns {text, block}, asking once for a missing block."""
        acct = accounts[role]
        who = f"{acct['id']} ({ROLE_NAMES[role]})"
        write(f"Rodada {state['round']} · loop → {who}", text)
        request = text
        # The reviewer keeps Bash for the tests, so it could still edit or commit:
        # a turn that changed the branch does not count, whatever its verdict.
        before = tree_state(area) if role == "reviewer" else None
        for _ in range(2):
            end = converse(acct, state["sessions"][role], request, folder=area["tree"],
                           role_prompt=ROLE_DEV if role == "dev" else ROLE_REVIEWER, reviewer=role == "reviewer",
                           model=models[role], permission=permission,
                           on_tool=lambda name, data: say(f"  · {acct['id']} {name}: {tool_summary(name, data)}"))
            state["sessions"][role] = end["session"] or state["sessions"][role]
            state["cost_usd"] = round(state["cost_usd"] + (end["cost_usd"] or 0), 4)
            save()
            changes = tree_changes(before, tree_state(area)) if before is not None else ""
            if changes:
                write(f"Rodada {state['round']} · loop", f"O revisor mudou o branch durante o turno dele: {changes}.")
                raise Stop("reviewer_edited", f"o revisor mudou o branch durante o turno dele ({changes}); "
                                              "o veredito não conta")
            if not end["ok"]:
                raise Stop("failed", f"a conta {who} não respondeu: {end['error']}")
            answer = end["answer"] or ""
            write(f"Rodada {state['round']} · {who}", answer)
            block = loop_block(answer)
            if block is not None:
                return {"text": without_block(answer), "block": block}
            request = ASK_FOR_BLOCK
        raise Stop("failed", f"{who} não fechou a resposta com o bloco loop-duo, nem depois do lembrete")

    def settle(role, said):
        """While `role` asks, takes the questions to the other agent and brings the answers back."""
        exchanges = 0
        while said["block"]["state"] == "question":
            exchanges += 1
            questions = questions_of(said["block"])
            if exchanges > QUESTIONS_PER_ROUND or not questions:
                reason = "perguntas demais numa rodada" if questions else "uma pergunta veio sem texto"
                raise Stop("blocked", f"{ROLE_NAMES[role]}: {reason}")
            other = "reviewer" if role == "dev" else "dev"
            say(f"  ? o {ROLE_NAMES[role]} pergunta ao {ROLE_NAMES[other]}: " + "; ".join(q["question"] for q in questions))
            reply = speak(other, f"The {'developer' if role == 'dev' else 'reviewer'} asked (round {state['round']}):\n\n"
                                 f"{quote_questions(questions)}\n\nAnswer with the \"answer\" state, one answer "
                                 "per question, in the same order. Do not go on with other work now.")
            answers = texts(reply["block"].get("answers")) or [reply["text"] or "(no answer)"]
            pairs = "\n".join(f"{n}. {q['question']}\n   → {a}" for n, (q, a) in
                              enumerate(zip(questions, answers + [""] * len(questions)), start=1))
            comment = f"\n\nComment from the other agent:\n{reply['text']}" if reply["text"] else ""
            said = speak(role, f"Answers from the other agent:\n\n{pairs}{comment}\n\nGo on from where you stopped.")
        return said

    def hand_in(text):
        """The developer's turn until it hands in the round: its summary, or Stop when it is blocked."""
        said = settle("dev", speak("dev", text))
        if said["block"]["state"] == "blocked":
            raise Stop("blocked", str(said["block"].get("reason") or said["text"] or "sem motivo informado"))
        return str(said["block"].get("summary") or said["text"][-1500:])

    say(f"loop-duo {loop_id}: {dev} desenvolve, {reviewer} revisa · branch {area['branch']} · {area['tree']}")
    write("Objetivo", goal)
    save()
    summary, verdict = "", ""
    request = (f"LOOP GOAL:\n\n{goal.strip()}\n\nYou are on the {area['branch']} branch, created from "
               f"{area['base'][:10]}. Start. When the round is done, commit and close with the \"review\" state.")
    try:
        for round_number in range(1, rounds + 1):
            state["round"] = round_number
            say(f"rodada {round_number}: {dev} desenvolve")
            summary = hand_in(request)
            untracked = untracked_files(area)
            if untracked:
                # Once per round: whatever is still untracked stays out of every commit.
                say(f"rodada {round_number}: {len(untracked)} caminho(s) não versionado(s) de volta ao desenvolvedor")
                summary += "\n\n" + hand_in(
                    "These paths are not tracked by git, and the loop never commits untracked files:\n\n"
                    + "\n".join(f"- {path}" for path in untracked)
                    + "\n\nCommit the ones that belong to the change. Add the others to .gitignore or delete "
                    "them. Then close with \"review\" again.")
                untracked = untracked_files(area)
            commit_leftovers(area, round_number)
            commits = git(area["tree"], "log", "--oneline", f"{area['base']}..HEAD", check=False) or "(none)"
            changed = git(area["tree"], "diff", "--stat", area["base"], check=False) or "(nothing)"
            left_out = ("\n\nUntracked paths, left out of every commit:\n" + "\n".join(f"- {path}" for path in untracked)
                        if untracked else "")
            say(f"rodada {round_number}: {reviewer} revisa")
            review = settle("reviewer", speak("reviewer", (
                f"LOOP GOAL:\n\n{goal.strip()}\n\nROUND {round_number}. The developer handed in:\n\n{summary}\n\n"
                f"Commits since the base ({area['base'][:10]}):\n{commits}\n\nChanged files:\n{changed}{left_out}\n\n"
                f"Review (git diff {area['base'][:10]}) and close with \"approved\" or \"changes\".")))
            if review["block"]["state"] == "approved":
                verdict = str(review["block"].get("summary") or review["text"] or "approved")
                state["status"] = "approved"
                break
            comments = texts(review["block"].get("comments"))
            say(f"rodada {round_number}: mudanças pedidas ({len(comments)})")
            if cost_max is not None and state["cost_usd"] >= cost_max:
                raise Stop("cost_ceiling", f"o custo estimado chegou a US$ {state['cost_usd']:.2f} "
                                           f"(teto US$ {cost_max:.2f})")
            items = "\n".join(f"- {c}" for c in comments) or "(no items)"
            request = (f"The reviewer requested changes in round {round_number}:\n\n{items}\n\nFull review:\n\n"
                       f"{review['text'] or '(no text)'}\n\nFix them, commit and close with \"review\".")
        else:
            raise Stop("round_ceiling", f"o revisor não aprovou em {rounds} rodada(s)")
    except Stop as stop:
        state["status"], state["reason"] = stop.status, stop.reason
    except Refused as error:
        # An error mid-round (a vanished session, git refused) closes the loop with the state saved.
        state["status"], state["reason"] = "failed", str(error)

    if state["status"] == "approved" and until == "pr":
        try:
            state["pr"] = open_pr(state, summary, verdict)
        except Refused as error:
            state["pr_error"] = str(error)
    state["finished"] = iso(now())
    state["commits"] = git(area["tree"], "log", "--oneline", f"{area['base']}..HEAD", check=False).splitlines()
    save()
    return {"ok": state["status"] == "approved", **{k: v for k, v in state.items() if k != "goal"}}


def tool_summary(name, data):
    for key in ("command", "file_path", "pattern", "description"):
        if isinstance(data.get(key), str):
            return data[key].strip().splitlines()[0][:100] if data[key].strip() else ""
    return ""


# ---------------------------------------------------------------- listing


def recent_loops(limit=10):
    out = []
    for path in sorted(LOOPS.glob("*/loop.json"), reverse=True)[:limit]:
        data = read_json(path)
        if not isinstance(data, dict) or not LOOP_ID.match(str(data.get("id"))):
            continue
        item = {key: data.get(key) for key in ("id", "dev", "reviewer", "status", "reason", "round", "max_rounds",
                                               "until", "pr", "pr_error", "cost_usd", "started", "finished",
                                               "conversation")}
        item["branch"] = (data.get("area") or {}).get("branch")
        item["tree"] = (data.get("area") or {}).get("tree")
        # "running" with its process gone (a restart, a kill): the loop does not resume by itself.
        if item["status"] == "running" and not process_alive(data.get("pid")):
            item["status"] = "interrupted"
        out.append(item)
    return out


# ---------------------------------------------------------------- command line


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="loop-duo", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="roda um loop até o revisor aprovar ou um teto parar")
    run.add_argument("goal", help='o objetivo do loop; "-" lê da entrada padrão')
    run.add_argument("--dev", choices=ACCOUNTS, default="original")
    run.add_argument("--reviewer", choices=ACCOUNTS, default="duo")
    run.add_argument("--repo", help="o repositório (padrão: a pasta atual)")
    run.add_argument("--no-worktree", action="store_true", help="cria o branch na própria pasta, sem worktree")
    run.add_argument("--rounds", type=int, default=6)
    run.add_argument("--until", choices=LEVELS, default="local", help="pr: push e PR em rascunho depois da aprovação")
    run.add_argument("--max-cost", type=float, help="para quando o custo estimado (US$) chega a este teto")
    run.add_argument("--model-dev")
    run.add_argument("--model-reviewer")
    run.add_argument("--permission", choices=PERMISSION_MODES, default="auto")
    run.add_argument("--json", action="store_true")
    listing = commands.add_parser("list", help="lista os loops recentes")
    listing.add_argument("--limit", type=int, default=10)
    listing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "list":
        loops = recent_loops(args.limit)
        if args.json:
            print(json.dumps({"loops": loops}, ensure_ascii=False, indent=2))
        elif not loops:
            print("Nenhum loop ainda.")
        for item in [] if args.json else loops:
            extra = f" · PR {item['pr']}" if item.get("pr") else ""
            print(f"{item['id']}  {item['status']:<15} {item['dev']} → {item['reviewer']}  "
                  f"rodada {item['round']}/{item['max_rounds']}  US$ {item['cost_usd'] or 0:.2f}  {item['branch']}{extra}")
        return 0

    goal = sys.stdin.read() if args.goal == "-" else args.goal
    say = (lambda text: None) if args.json else print
    try:
        result = run_loop(goal, dev=args.dev, reviewer=args.reviewer, folder=args.repo, worktree=not args.no_worktree,
                          rounds=args.rounds, until=args.until, cost_max=args.max_cost, model_dev=args.model_dev,
                          model_reviewer=args.model_reviewer, permission=args.permission, say=say)
    except Refused as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"loop-duo: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = result["status"]
        print(("aprovado" if result["ok"] else f"parado ({status})") +
              f" na rodada {result['round']} · US$ {result['cost_usd']:.2f} estimado · branch {result['area']['branch']}")
        if result.get("reason"):
            print(f"motivo: {result['reason']}")
        if result.get("pr") or result.get("pr_error"):
            print(f"PR: {result.get('pr') or result.get('pr_error')}")
        print(f"conversa: {result['conversation']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
