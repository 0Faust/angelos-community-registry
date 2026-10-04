#!/usr/bin/env python3
"""Desktop GUI for AngelOS Community Registry moderation."""
import base64
import html
import json
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk

REPO = "futureUnd1ground/angelos-community-registry"
MODERATORS_URL = "https://raw.githubusercontent.com/{}/main/moderators.json".format(REPO)


def gh(args):
    result = subprocess.run(["gh", "api"] + args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub API error")
    return json.loads(result.stdout)


def current_user():
    result = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("GitHub login required: run gh auth login")
    return result.stdout.strip()


def require_moderator():
    user = current_user()
    request = urllib.request.Request(MODERATORS_URL, headers={"User-Agent": "angelos-community-registry-gui"})
    with urllib.request.urlopen(request, timeout=20) as response:
        allowed = json.loads(response.read(64 * 1024).decode("utf-8"))
    if user not in allowed:
        raise RuntimeError("@{} is not an approved registry moderator".format(user))
    return user


def candidate_entries(pr):
    try:
        head = pr["head"]["repo"]["full_name"]
        sha = pr["head"]["sha"]
        content = gh(["repos/{}/contents/plugins.json?ref={}".format(head, sha)])
        decoded = base64.b64decode(content["content"]).decode("utf-8")
        payload = json.loads(decoded)
        return payload.get("plugins", []) if isinstance(payload, dict) else []
    except Exception as exc:
        return [{"id": "pr-{}".format(pr.get("number")), "name": pr.get("title", "Unreadable PR"),
                 "description": "Cannot read plugins.json: {}".format(exc), "status": "pending"}]


def fetch_prs():
    prs = gh(["repos/{}/pulls?state=open&base=main&per_page=100".format(REPO)])
    rows = []
    for pr in prs:
        entries = candidate_entries(pr)
        for entry in entries[:20]:
            row = dict(entry)
            row["_pr"] = pr
            rows.append(row)
    return rows


def pr_action(number, action):
    if action == "review":
        return gh(["repos/{}/pulls/{}/reviews".format(REPO, number), "-X", "POST", "-f", "event=APPROVE", "-f", "body=Approved by registry desktop moderator."])
    if action == "reject":
        gh(["repos/{}/issues/{}/comments".format(REPO, number), "-X", "POST", "-f", "body=Rejected by registry desktop moderator."])
        return gh(["repos/{}/pulls/{}".format(REPO, number), "-X", "PATCH", "-f", "state=closed"])
    if action == "merge":
        return gh(["repos/{}/pulls/{}/merge".format(REPO, number), "-X", "PUT", "-f", "merge_method=squash"])
    raise RuntimeError("Unknown action")


class RegistryApp(tk.Tk):
    def __init__(self, user):
        super().__init__()
        self.title("AngelOS Community Registry Moderator")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.user = user
        self.rows = []
        self.row_by_iid = {}
        self.search_value = tk.StringVar()
        self.status = tk.StringVar(value="Ready")
        self._build()
        self.refresh()

    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # Keep every control in one dark palette. Native ttk buttons otherwise
        # remain light on some Linux themes even when the window is dark.
        palette = {"bg": "#17191c", "panel": "#22252a", "fg": "#f3f5f7", "muted": "#aeb6c2",
                   "select": "#315b82", "border": "#4c5561", "accent": "#73b7ff"}
        self.configure(background=palette["bg"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
        style.configure("TButton", background=palette["panel"], foreground=palette["fg"], bordercolor=palette["border"],
                        lightcolor=palette["panel"], darkcolor=palette["panel"], padding=(10, 6))
        style.map("TButton", background=[("active", palette["select"]), ("pressed", palette["select"])],
                  foreground=[("disabled", palette["muted"]), ("!disabled", palette["fg"])])
        style.configure("TEntry", fieldbackground=palette["panel"], foreground=palette["fg"], bordercolor=palette["border"])
        style.configure("Treeview", background=palette["panel"], fieldbackground=palette["panel"], foreground=palette["fg"], rowheight=30)
        style.map("Treeview", background=[("selected", palette["select"])], foreground=[("selected", palette["fg"])])
        style.configure("Treeview.Heading", background=palette["panel"], foreground=palette["fg"], relief="flat")
        header = ttk.Frame(self, padding=(16, 14))
        header.pack(fill="x")
        ttk.Label(header, text="COMMUNITY REGISTRY", font=("Sans", 18, "bold")).pack(side="left")
        ttk.Label(header, text="  Только для модераторов  •  @{}".format(self.user), foreground="#ff7b72").pack(side="left", padx=12)
        ttk.Button(header, text="Обновить", command=self.refresh).pack(side="right")
        search = ttk.Frame(self, padding=(16, 0, 16, 10))
        search.pack(fill="x")
        ttk.Label(search, text="Поиск:").pack(side="left")
        field = ttk.Entry(search, textvariable=self.search_value, width=45)
        field.pack(side="left", padx=8)
        field.bind("<KeyRelease>", lambda _event: self.render_rows())
        ttk.Button(search, text="Очистить", command=lambda: self.search_value.set("") or self.render_rows()).pack(side="left")
        self.count_label = ttk.Label(search, text="")
        self.count_label.pack(side="right")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(16, 0, 0, 0))
        body.add(left, weight=3); body.add(right, weight=2)
        columns = ("kind", "status", "name", "version", "author", "pr")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {"kind": "Источник", "status": "Статус", "name": "Плагин", "version": "Версия", "author": "Автор", "pr": "PR"}
        widths = {"kind": 80, "status": 90, "name": 220, "version": 85, "author": 150, "pr": 55}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_details())

        ttk.Label(right, text="Детали заявки", font=("Sans", 14, "bold")).pack(anchor="w")
        self.details = tk.Text(right, wrap="word", height=20, state="disabled", background=palette["panel"], foreground=palette["fg"], insertbackground=palette["fg"], relief="flat")
        self.details.pack(fill="both", expand=True, pady=(10, 12))
        actions = ttk.Frame(right)
        actions.pack(fill="x")
        for label, action in (("Открыть PR", self.open_pr), ("Одобрить review", lambda: self.action("review")),
                              ("Отклонить", lambda: self.action("reject")), ("Merge", lambda: self.action("merge"))):
            ttk.Button(actions, text=label, command=action).pack(fill="x", pady=3)
        status_bar = ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=6)
        status_bar.pack(fill="x", side="bottom")

    def set_status(self, text):
        self.status.set(text)

    def refresh(self):
        self.set_status("Загрузка открытых PR...")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            rows = fetch_prs()
            self.after(0, lambda: self._loaded(rows))
        except Exception as exc:
            self.after(0, lambda: self._failed(str(exc)))

    def _loaded(self, rows):
        self.rows = rows
        self.render_rows()
        self.set_status("Загружено заявок: {}".format(len(rows)))

    def _failed(self, message):
        self.set_status("Ошибка: " + message)
        messagebox.showerror("Community Registry", message)

    def render_rows(self):
        query = self.search_value.get().casefold().strip()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.row_by_iid = {}
        shown = 0
        for row in self.rows:
            pr = row["_pr"]
            haystack = " ".join(str(row.get(key, "")) for key in ("id", "name", "author", "description", "category", "tags"))
            haystack += " " + str(pr.get("title", "")) + " " + str(pr.get("number", ""))
            if query and query not in haystack.casefold():
                continue
            iid = self.tree.insert("", "end", values=("GitHub PR", row.get("status", "pending"), row.get("name", row.get("id", "-")), row.get("version", "-"), row.get("author", pr.get("user", {}).get("login", "-")), "#{}".format(pr.get("number"))))
            self.row_by_iid[iid] = row
            shown += 1
        self.count_label.configure(text="{} / {}".format(shown, len(self.rows)))
        if shown:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first); self.tree.focus(first); self.show_details()

    def selected(self):
        selected = self.tree.selection()
        return self.row_by_iid.get(selected[0]) if selected else None

    def show_details(self):
        row = self.selected()
        self.details.configure(state="normal"); self.details.delete("1.0", "end")
        if row:
            pr = row["_pr"]
            values = ["{} v{}".format(row.get("name", row.get("id", "-")), row.get("version", "-")),
                      "ID: {}".format(row.get("id", "-")), "Статус: {}".format(row.get("status", "pending")),
                      "Контрибьютор PR: @{}".format(pr.get("user", {}).get("login", "-")),
                      "Автор плагина: {}".format(row.get("author", "-")), "Описание: {}".format(row.get("description", "-")),
                      "Теги: {}".format(", ".join(map(str, row.get("tags", []))) or "-"),
                      "Категория: {}".format(row.get("category", "-")), "Source: {}".format(row.get("source", "-")),
                      "Repository: {}".format(row.get("repository", "-")), "License: {}".format(row.get("license", "-")),
                      "PR: {}".format(pr.get("html_url", "-"))]
            self.details.insert("1.0", "\n".join(values))
        self.details.configure(state="disabled")

    def open_pr(self):
        row = self.selected()
        if row:
            webbrowser.open(row["_pr"].get("html_url"))

    def action(self, action):
        row = self.selected()
        if not row:
            return
        number = row["_pr"].get("number")
        labels = {"review": "одобрить review", "reject": "закрыть PR", "merge": "слить PR"}
        if not messagebox.askyesno("Подтверждение", "Точно {} #{}?".format(labels[action], number)):
            return
        self.set_status("Выполняется действие...")
        threading.Thread(target=self._action, args=(number, action), daemon=True).start()

    def _action(self, number, action):
        try:
            pr_action(number, action)
            self.after(0, lambda: (self.set_status("Готово: PR #{}".format(number)), self.refresh()))
        except Exception as exc:
            self.after(0, lambda: self._failed(str(exc)))


def main():
    try:
        user = require_moderator()
    except Exception as exc:
        root = tk.Tk(); root.withdraw(); messagebox.showerror("Доступ запрещён", str(exc)); root.destroy(); return 1
    app = RegistryApp(user)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
