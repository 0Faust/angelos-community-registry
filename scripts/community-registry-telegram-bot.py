#!/usr/bin/env python3
"""Telegram inbox and moderation bot for the AngelOS community registry.

Required environment: TELEGRAM_BOT_TOKEN.
Required for moderation actions: GITHUB_TOKEN with contents write, pull request write, and issues write access.
"""
import json
import base64
import html
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = "futureUnd1ground/angelos-community-registry"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = Path(__file__).resolve().parents[1]
MODERATORS_FILE = ROOT / "telegram-moderators.json"
STATE_FILE = Path(os.environ.get("ANGELOS_TELEGRAM_STATE", "~/.local/state/angelos-community-registry/telegram.json")).expanduser()
POLL_SECONDS = max(10, int(os.environ.get("TELEGRAM_POLL_SECONDS", "30")))
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILES = 2000


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
    timeout = max(40, int((payload or {}).get("timeout", 0)) + 10)
    response = http_json("https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method), "POST", payload, timeout=timeout)
    if not response.get("ok"):
        raise RuntimeError(response.get("description", "Telegram API request failed"))
    return response


def safe_error(error):
    text = str(error)
    for secret in (TG_TOKEN, GH_TOKEN):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def moderators():
    values = json.loads(MODERATORS_FILE.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise RuntimeError("telegram-moderators.json must contain a JSON array")
    return {str(item).lstrip("@").casefold() for item in values if str(item).strip()}


def is_moderator(message):
    username = str((message.get("from") or {}).get("username") or "")
    return username.casefold() in moderators()


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "seen": {}, "chats": {}, "metadata_cache": {}}
    if not isinstance(state, dict):
        return {"offset": 0, "seen": {}, "chats": {}, "metadata_cache": {}}
    # Old versions stored chat IDs without their owner. Discard these so only
    # explicitly re-subscribed moderators receive registry content.
    if not isinstance(state.get("chats"), dict):
        state["chats"] = {}
    if not isinstance(state.get("seen"), dict):
        state["seen"] = {}
    if not isinstance(state.get("metadata_cache"), dict):
        state["metadata_cache"] = {}
    state.setdefault("offset", 0)
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, STATE_FILE)


def pr_items():
    result = []
    page = 1
    while True:
        items = github("/repos/{}/pulls?state=open&base=main&per_page=100&page={}".format(REPO, page))
        result.extend(items)
        if len(items) < 100:
            return result
        page += 1


def registry_snapshot(repository, ref):
    content = github("/repos/{}/contents/plugins.json?ref={}".format(repository, ref))
    decoded = base64.b64decode(content["content"]).decode("utf-8")
    payload = json.loads(decoded)
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise RuntimeError("Invalid plugins.json registry format")
    result = {}
    for item in payload["plugins"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise RuntimeError("Registry contains a plugin without an ID")
        plugin_id = str(item["id"])
        if plugin_id in result:
            raise RuntimeError("Registry contains duplicate plugin ID: {}".format(plugin_id))
        result[plugin_id] = item
    return result, content.get("sha", "")


def registry_plugins(repository, ref):
    return registry_snapshot(repository, ref)[0]


def changed_plugins(pr, base_plugins):
    try:
        head = pr.get("head", {})
        repository = head.get("repo", {}).get("full_name")
        if not repository:
            raise RuntimeError("PR source repository is unavailable")
        candidate = registry_plugins(repository, head["sha"])
        changes = []
        for plugin_id in sorted(set(base_plugins) | set(candidate)):
            before, after = base_plugins.get(plugin_id), candidate.get(plugin_id)
            if before == after:
                continue
            plugin = dict(after or before)
            plugin["_change"] = "added" if before is None else "removed" if after is None else "updated"
            plugin["_removed"] = after is None
            changes.append(plugin)
        return changes, None
    except Exception as exc:
        return [], str(exc)


def fetch_prs(state=None):
    base_plugins, base_sha = registry_snapshot(REPO, "main")
    prs = pr_items()
    rows = []
    cache = state.setdefault("metadata_cache", {}) if state is not None else {}
    active = set()
    for pr in prs:
        number = str(pr.get("number"))
        active.add(number)
        head_sha = pr.get("head", {}).get("sha", "")
        cached = cache.get(number, {})
        if not isinstance(cached, dict):
            cached = {}
        if cached.get("head_sha") == head_sha and cached.get("base_sha") == base_sha:
            changes, error = cached.get("changes", []), cached.get("error")
        else:
            changes, error = changed_plugins(pr, base_plugins)
            cache[number] = {"head_sha": head_sha, "base_sha": base_sha,
                             "changes": changes, "error": error}
        pr["_plugin_changes"] = changes
        pr["_registry_error"] = error
        pr["_base_sha"] = base_sha
        rows.append(pr)
    for number in list(cache):
        if number not in active:
            del cache[number]
    return rows


def validate_archive(entry):
    source = str(entry.get("source", ""))
    parsed_source = urllib.parse.urlparse(source)
    if parsed_source.scheme != "https" or parsed_source.hostname != "github.com":
        raise RuntimeError("Plugin source must be a GitHub HTTPS release URL")
    request = urllib.request.Request(source, headers={"User-Agent": "angelos-registry-telegram-moderator"})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive_data = response.read(MAX_ARCHIVE + 1)
        final_url = urllib.parse.urlparse(response.geturl())
    final_host = final_url.hostname or ""
    if final_url.scheme != "https" or not (final_host == "github.com" or final_host.endswith(".githubusercontent.com")):
        raise RuntimeError("Plugin download redirected to an untrusted host")
    if len(archive_data) > MAX_ARCHIVE:
        raise RuntimeError("Archive exceeds 64 MiB")
    with TemporaryDirectory(prefix="angelos-telegram-review-") as temp:
        root = Path(temp) / "unpacked"
        root.mkdir()
        archive_path = Path(temp) / "plugin.zip"
        archive_path.write_bytes(archive_data)
        with zipfile.ZipFile(archive_path) as archive:
            items = archive.infolist()
            if len(items) > MAX_FILES:
                raise RuntimeError("Archive has too many files")
            if sum(item.file_size for item in items) > MAX_ARCHIVE:
                raise RuntimeError("Expanded archive exceeds 64 MiB")
            for item in items:
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError("Archive contains a symbolic link")
                target = (root / item.filename).resolve()
                if not str(target).startswith(str(root.resolve()) + os.sep):
                    raise RuntimeError("Archive contains an unsafe path")
            archive.extractall(root)
        manifests = list(root.rglob("manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError("Archive must contain exactly one manifest.json")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        if manifest.get("id") != entry.get("id"):
            raise RuntimeError("Manifest ID does not match registry entry")
        if str(manifest.get("version", "")) != str(entry.get("version", "")):
            raise RuntimeError("Manifest version does not match registry entry")
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise RuntimeError("Manifest name is missing")


def approve_registry_entries(plugin_ids, pr_number):
    response = github("/repos/{}/contents/plugins.json?ref=main".format(REPO))
    payload = json.loads(base64.b64decode(response["content"]).decode("utf-8"))
    found = set()
    changed = False
    for entry in payload.get("plugins", []):
        if entry.get("id") in plugin_ids:
            found.add(entry["id"])
            if entry.get("status") != "approved":
                entry["status"] = "approved"
                changed = True
    missing = set(plugin_ids) - found
    if missing:
        raise RuntimeError("Merged registry is missing plugin entries: {}".format(", ".join(sorted(missing))))
    if not changed:
        return ""
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    result = github("/repos/{}/contents/plugins.json".format(REPO), "PUT", {
        "message": "Approve plugin listings from PR #{}".format(pr_number),
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": response["sha"],
        "branch": "main"
    })
    return result.get("commit", {}).get("sha", "")


def format_pr(pr):
    esc = lambda value, limit: html.escape(str(value if value is not None else "-")[:limit])
    changes = pr.get("_plugin_changes", [])
    error = pr.get("_registry_error")
    if error:
        review = "plugins.json не удалось прочитать: {}".format(error)
    elif not changes:
        review = "Изменений в plugins.json нет; это PR без заявки на плагин."
    else:
        review = "Изменения записей плагинов: {}".format(len(changes))
    lines = ["<b>Открытый Pull Request #{}: {}</b>".format(pr.get("number"), esc(pr.get("title"), 100)),
             "Контрибьютор: @{}".format(esc(pr.get("user", {}).get("login", "-"), 50)),
             "Ветка: {}".format(esc(pr.get("head", {}).get("label", "-"), 100)),
             "Состояние: {}".format(esc(review, 220)),
             "Описание PR: {}".format(esc(pr.get("body") or "-", 400))]
    return "\n".join(lines)


def format_plugin(entry):
    esc = lambda value, limit: html.escape(str(value if value is not None else "-")[:limit])
    def list_value(key):
        value = entry.get(key)
        if isinstance(value, list):
            return ", ".join(map(str, value)) or "-"
        return str(value) if value else "-"

    change = {"added": "Добавлен", "updated": "Обновлён", "removed": "Удаляется"}.get(entry.get("_change"), "Изменён")
    lines = ["<b>{}: {} v{}</b>".format(change, esc(entry.get("name", entry.get("id", "-")), 50), esc(entry.get("version", "-"), 15)),
             "ID: {}".format(esc(entry.get("id", "-"), 40)),
             "Автор плагина: {}".format(esc(entry.get("author", "-"), 35)),
             "Описание: {}".format(esc(entry.get("description", "-") or "-", 140)),
             "Теги: {}".format(esc(list_value("tags"), 70)),
             "Категория: {}".format(esc(entry.get("category", "-"), 25)),
             "Source: {}".format(esc(entry.get("source", "-") or "-", 90)),
             "Repository: {}".format(esc(entry.get("repository", "-") or "-", 70)),
             "License: {}".format(esc(entry.get("license", "-"), 40)),
             "Dependencies: {}".format(esc(list_value("dependencies"), 50)),
             "Permissions: {}".format(esc(list_value("permissions"), 50))]
    return "\n".join(lines)


def send(chat_id, text):
    telegram("sendMessage", {"chat_id": chat_id, "text": html.escape(str(text)), "parse_mode": "HTML", "disable_web_page_preview": True})


def pr_keyboard(pr):
    number = str(pr.get("number"))
    return {"inline_keyboard": [
        [{"text": "Открыть PR", "url": pr.get("html_url", "https://github.com/{}/pulls/{}".format(REPO, number))}],
        [{"text": "Одобрить review", "callback_data": "pr:approve:" + number},
         {"text": "Отклонить", "callback_data": "pr:reject:" + number}],
        [{"text": "Merge", "callback_data": "pr:merge:" + number},
         {"text": "Обновить", "callback_data": "pr:refresh:" + number}],
    ]}


def send_pr(chat_id, pr):
    telegram("sendMessage", {"chat_id": chat_id, "text": format_pr(pr), "parse_mode": "HTML",
                              "disable_web_page_preview": True, "reply_markup": pr_keyboard(pr)})
    for entry in pr.get("_plugin_changes", []):
        telegram("sendMessage", {"chat_id": chat_id, "text": format_plugin(entry), "parse_mode": "HTML",
                                  "disable_web_page_preview": True})
    if pr.get("_registry_error"):
        send(chat_id, "Не удалось проверить метаданные плагина. Перед merge исправь plugins.json.")


def help_text():
    return ("Команды: /inbox — открытые заявки; /status — состояние бота; "
            "/approve N — одобрить review PR; /reject N — закрыть PR; "
            "/merge N — проверить пакеты и слить PR; /stop — отключить уведомления. "
            "Можно пользоваться кнопками под заявкой. "
            "Модерация доступна только allowlist.")


def moderate(message, command, number):
    if not is_moderator(message):
        send(message["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    try:
        pr = github("/repos/{}/pulls/{}".format(REPO, int(number)))
        if pr.get("state") != "open" or pr.get("base", {}).get("ref") != "main":
            raise RuntimeError("PR must be open and target main")
        if command == "approve":
            github("/repos/{}/pulls/{}/reviews".format(REPO, number), "POST", {
                "event": "APPROVE", "body": "Approved by registry Telegram moderator.",
                "commit_id": pr.get("head", {}).get("sha")
            })
            result = "PR #{} одобрен review.".format(number)
        elif command == "reject":
            github("/repos/{}/issues/{}/comments".format(REPO, number), "POST", {"body": "Rejected by registry Telegram moderator."})
            github("/repos/{}/pulls/{}".format(REPO, number), "PATCH", {"state": "closed"})
            result = "PR #{} закрыт.".format(number)
        elif command == "merge":
            base_plugins = registry_plugins(REPO, "main")
            changes, registry_error = changed_plugins(pr, base_plugins)
            if registry_error:
                raise RuntimeError("Cannot validate PR plugin metadata: {}".format(registry_error))
            if not changes:
                raise RuntimeError("Merge blocked: PR has no changed plugin entry to validate")
            plugin_changes = [entry for entry in changes if not entry.get("_removed")]
            for entry in plugin_changes:
                validate_archive(entry)
            merged = github("/repos/{}/pulls/{}/merge".format(REPO, number), "PUT", {
                "merge_method": "squash", "sha": pr.get("head", {}).get("sha")
            })
            if not merged.get("merged"):
                raise RuntimeError(merged.get("message", "GitHub did not merge the pull request"))
            try:
                approved_ids = [entry["id"] for entry in plugin_changes]
                if approved_ids:
                    approve_registry_entries(approved_ids, number)
                removed_count = sum(1 for entry in changes if entry.get("_removed"))
                result = "PR #{} смёржен; {} плагинов активированы, удалено записей: {}.".format(
                    number, len(approved_ids), removed_count)
            except Exception as exc:
                result = "PR #{} смёржен, архивы проверены, но статус остался pending: {}".format(number, exc)
        else:
            result = "Неизвестная команда. " + help_text()
        send(message["chat"]["id"], result)
    except Exception as exc:
        send(message["chat"]["id"], "Ошибка GitHub: {}".format(safe_error(exc)))


def handle_update(update, state):
    message = update.get("message") or {}
    text = str(message.get("text", "")).strip()
    chat_id = message.get("chat", {}).get("id")
    if not chat_id or not text:
        return
    parts = text.split()
    command = parts[0].split("@", 1)[0].casefold()
    if not is_moderator(message):
        send(chat_id, "Доступ запрещён: добавьте свой Telegram username в список модераторов.")
        return
    if message.get("chat", {}).get("type") != "private":
        send(chat_id, "Команды модерации работают только в личном чате с ботом.")
        return
    if command == "/start":
        username = str((message.get("from") or {}).get("username") or "")
        key = str(chat_id)
        if state["chats"].get(key) != username:
            state["chats"][key] = username
            send(chat_id, "Этот чат добавлен для уведомлений о новых и изменённых PR.")
        send(chat_id, "AngelOS Community Registry bot\n" + help_text())
    elif command == "/help":
        send(chat_id, help_text())
    elif command == "/stop":
        state["chats"].pop(str(chat_id), None)
        send(chat_id, "Уведомления отключены для этого чата.")
    elif command == "/inbox":
        prs = fetch_prs(state)
        send(chat_id, "Открытых PR: {}".format(len(prs)))
        for pr in prs:
            send_pr(chat_id, pr)
    elif command == "/status":
        send(chat_id, "Бот работает. Открытых PR: {}. Уведомления: {}.".format(len(fetch_prs(state)), "включены" if str(chat_id) in state["chats"] else "выключены"))
    elif command in ("/approve", "/reject", "/merge") and len(parts) == 2 and parts[1].isdigit():
        moderate(message, command[1:], parts[1])
    else:
        send(chat_id, help_text())


def handle_callback(update, state):
    callback = update.get("callback_query") or {}
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    actor = {"from": callback.get("from") or {}, "chat": message.get("chat") or {}}
    callback_id = callback.get("id")
    if callback_id:
        telegram("answerCallbackQuery", {"callback_query_id": callback_id})
    if data.startswith("pr:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = parts[1], parts[2]
    elif data.startswith("confirm:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = "confirm:" + parts[1], parts[2]
    elif data.startswith("cancel:") and data.split(":", 1)[1].isdigit():
        action, number = "cancel", data.split(":", 1)[1]
    else:
        return
    if not is_moderator(actor):
        if actor["chat"].get("id"):
            send(actor["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    if actor["chat"].get("type") != "private":
        send(actor["chat"].get("id"), "Кнопки модерации работают только в личном чате с ботом.")
        return
    if action == "refresh":
        for pr in fetch_prs(state):
            if str(pr.get("number")) == number:
                send_pr(actor["chat"].get("id"), pr)
                return
        send(actor["chat"].get("id"), "PR #{} больше не открыт.".format(number))
        return
    if action in ("reject", "merge"):
        prompt = "PR #{}: подтвердить действие {}?".format(number, action)
        markup = {"inline_keyboard": [[
            {"text": "Подтвердить", "callback_data": "confirm:{}:{}".format(action, number)},
            {"text": "Отмена", "callback_data": "cancel:{}".format(number)},
        ]]}
        telegram("sendMessage", {"chat_id": actor["chat"].get("id"), "text": prompt, "reply_markup": markup})
    elif action == "approve":
        moderate(actor, action, number)
    elif action.startswith("confirm:"):
        confirmed_action = action.split(":", 1)[1]
        moderate(actor, confirmed_action, number)
    elif action == "cancel":
        send(actor["chat"].get("id"), "Действие отменено.")


def notify_new_prs(state):
    prs = fetch_prs(state)
    allowed = moderators()
    state["chats"] = {chat_id: username for chat_id, username in state["chats"].items()
                       if str(username).casefold() in allowed}
    active = {str(pr["number"]): pr for pr in prs}
    for number, pr in active.items():
        signature = "{}:{}:{}".format(pr.get("updated_at", ""), pr.get("head", {}).get("sha", ""), pr.get("_base_sha", ""))
        if state["seen"].get(number) == signature:
            continue
        state["seen"][number] = signature
        for chat_id in state["chats"]:
            send_pr(int(chat_id), pr)
    state["seen"] = {number: value for number, value in state["seen"].items() if number in active}


def main():
    if not TG_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot")
    if not GH_TOKEN:
        raise SystemExit("Set GITHUB_TOKEN with contents write, pull request write, and issues write access")
    state = load_state()
    telegram("getMe")
    while True:
        try:
            response = telegram("getUpdates", {"offset": state["offset"] + 1, "timeout": POLL_SECONDS})
            for update in response.get("result", []):
                state["offset"] = update["update_id"]
                save_state(state)
                try:
                    if update.get("callback_query"):
                        handle_callback(update, state)
                    else:
                        handle_update(update, state)
                except Exception as exc:
                    print("update error: {}".format(safe_error(exc)), flush=True)
            try:
                notify_new_prs(state)
                save_state(state)
            except Exception as exc:
                print("registry polling error: {}".format(safe_error(exc)), flush=True)
        except (urllib.error.URLError, TimeoutError):
            print("network error while contacting Telegram", flush=True)
            time.sleep(5)
        except Exception as exc:
            print("bot error: {}".format(safe_error(exc)), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
