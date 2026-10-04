#!/usr/bin/env python3
"""Telegram inbox and moderation bot for the AngelOS community registry.

Required environment: TELEGRAM_BOT_TOKEN.
Optional: GITHUB_TOKEN for PR review/merge/close actions.
"""
import json
import base64
import html
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "futureUnd1ground/angelos-community-registry"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = Path(__file__).resolve().parents[1]
MODERATORS_FILE = ROOT / "telegram-moderators.json"
STATE_FILE = Path(os.environ.get("ANGELos_TELEGRAM_STATE", "~/.local/state/angelos-community-registry/telegram.json")).expanduser()
POLL_SECONDS = max(10, int(os.environ.get("TELEGRAM_POLL_SECONDS", "30")))


def http_json(url, method="GET", payload=None, headers=None, timeout=40):
    body = None
    request_headers = {"User-Agent": "angelos-community-registry-telegram-bot"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))


def github(path, method="GET", payload=None):
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = "Bearer " + GH_TOKEN
    return http_json("https://api.github.com" + path, method, payload, headers)


def telegram(method, payload=None):
    if not TG_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return http_json("https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method), "POST", payload)


def moderators():
    values = json.loads(MODERATORS_FILE.read_text(encoding="utf-8"))
    return {str(item).lstrip("@").casefold() for item in values if str(item).strip()}


def is_moderator(message):
    username = (message.get("from") or {}).get("username", "")
    return username.casefold() in moderators()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "seen": {}, "chats": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pr_items():
    return github("/repos/{}/pulls?state=open&base=main&per_page=100".format(REPO))


def candidate_details(pr):
    try:
        full_name = pr["head"]["repo"]["full_name"]
        sha = pr["head"]["sha"]
        content = github("/repos/{}/contents/plugins.json?ref={}".format(full_name, sha))
        decoded = base64.b64decode(content["content"]).decode("utf-8")
        payload = json.loads(decoded)
        return payload.get("plugins", []) if isinstance(payload, dict) else []
    except Exception:
        return []


def format_pr(pr):
    entries = candidate_details(pr)
    esc = lambda value: html.escape(str(value if value is not None else "-"))
    lines = ["<b>Открытый Pull Request</b>", "<b>PR #{}: {}</b>".format(pr.get("number"), esc(pr.get("title"))),
             "Контрибьютор: @{}".format(esc(pr.get("user", {}).get("login", "-"))),
             "Ссылка: {}".format(esc(pr.get("html_url", "-")))]
    if not entries:
        lines.append("plugins.json не удалось прочитать или он отсутствует.")
    for entry in entries[:10]:
        lines.extend(["", "<b>{}</b> v{}".format(esc(entry.get("name", entry.get("id", "-"))), esc(entry.get("version", "-"))),
                      "ID: {}".format(esc(entry.get("id", "-"))),
                      "Автор: {}".format(esc(entry.get("author", "-"))),
                      "Описание: {}".format(esc(entry.get("description", "-"))),
                      "Теги: {}".format(esc(", ".join(map(str, entry.get("tags", []))) or "-")),
                      "Категория: {}".format(esc(entry.get("category", "-"))),
                      "Source: {}".format(esc(entry.get("source", "-"))),
                      "Repository: {}".format(esc(entry.get("repository", "-"))),
                      "License: {}".format(esc(entry.get("license", "-")))])
    return "\n".join(lines)[:3900]


def send(chat_id, text):
    telegram("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def help_text():
    return ("Команды: /inbox — открытые заявки; /status — состояние бота; "
            "/approve N — одобрить review PR; /reject N — закрыть PR; "
            "/merge N — слить PR после review. Модерация доступна только allowlist.")


def moderate(message, command, number):
    if not is_moderator(message):
        send(message["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    try:
        pr = github("/repos/{}/pulls/{}".format(REPO, int(number)))
        if pr.get("state") != "open" or pr.get("base", {}).get("ref") != "main":
            raise RuntimeError("PR must be open and target main")
        if command == "approve":
            github("/repos/{}/pulls/{}/reviews".format(REPO, number), "POST", {"event": "APPROVE", "body": "Approved by registry Telegram moderator."})
            result = "PR #{} одобрен review.".format(number)
        elif command == "reject":
            github("/repos/{}/issues/{}/comments".format(REPO, number), "POST", {"body": "Rejected by registry Telegram moderator."})
            github("/repos/{}/pulls/{}".format(REPO, number), "PATCH", {"state": "closed"})
            result = "PR #{} закрыт.".format(number)
        elif command == "merge":
            merged = github("/repos/{}/pulls/{}/merge".format(REPO, number), "PUT", {"merge_method": "squash"})
            result = "PR #{}: {}".format(number, "смёржен" if merged.get("merged") else merged.get("message", "merge не выполнен"))
        else:
            result = "Неизвестная команда. " + help_text()
        send(message["chat"]["id"], result)
    except Exception as exc:
        send(message["chat"]["id"], "Ошибка GitHub: {}".format(exc))


def handle_update(update, state):
    message = update.get("message") or {}
    text = str(message.get("text", "")).strip()
    chat_id = message.get("chat", {}).get("id")
    if not chat_id or not text:
        return
    if chat_id not in state["chats"]:
        state["chats"].append(chat_id)
    parts = text.split()
    command = parts[0].split("@", 1)[0].casefold()
    if not is_moderator(message):
        send(chat_id, "Доступ запрещён: добавьте свой Telegram username в список модераторов.")
        return
    if command in ("/start", "/help"):
        if chat_id not in state["chats"]:
            state["chats"].append(chat_id)
            send(chat_id, "Этот чат добавлен для уведомлений о новых и изменённых PR.")
        send(chat_id, "AngelOS Community Registry bot\n" + help_text())
    elif command == "/inbox":
        prs = pr_items()
        send(chat_id, "Открытых PR: {}".format(len(prs)))
        for pr in prs:
            send(chat_id, format_pr(pr))
    elif command == "/status":
        send(chat_id, "Бот работает. Открытых PR: {}".format(len(pr_items())))
    elif command in ("/approve", "/reject", "/merge") and len(parts) == 2 and parts[1].isdigit():
        moderate(message, command[1:], parts[1])
    else:
        send(chat_id, help_text())


def notify_new_prs(state):
    prs = pr_items()
    active = {str(pr["number"]): pr for pr in prs}
    for number, pr in active.items():
        signature = "{}:{}".format(pr.get("updated_at", ""), pr.get("head", {}).get("sha", ""))
        if state["seen"].get(number) == signature:
            continue
        state["seen"][number] = signature
        for chat_id in state["chats"]:
            send(chat_id, format_pr(pr))
    state["seen"] = {number: value for number, value in state["seen"].items() if number in active}


def main():
    if not TG_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot")
    state = load_state()
    telegram("getMe")
    while True:
        try:
            notify_new_prs(state)
            response = telegram("getUpdates", {"offset": state["offset"] + 1, "timeout": POLL_SECONDS})
            for update in response.get("result", []):
                state["offset"] = update["update_id"]
                handle_update(update, state)
            save_state(state)
        except (urllib.error.URLError, TimeoutError) as exc:
            print("network error: {}".format(exc), flush=True)
            time.sleep(5)
        except Exception as exc:
            print("bot error: {}".format(exc), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
