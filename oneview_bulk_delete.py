#!/usr/bin/env python3
"""
ThreatDown OneView (MSP) - Bulk Endpoint Delete

1. Enter an OAuth2 client ID / secret (OneView > Integrate) and click Connect.
2. Pick a site.
3. Paste computer names (one per line, or comma/space separated).
4. Click "Look up" to match them against the site's endpoints.
5. Click "Delete matched" (leave "Dry run" ticked to test without deleting).

Requires: pip install requests
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from oneview_api import (Config, Endpoint, OneViewClient, Site, endpoint_id,
                         endpoint_label, match_names, parse_names)

CONFIG_PATH = Path(__file__).with_name("config.json")
UNREGISTER_ISOLATED = False
DELETE_DELAY_SEC = 0.25  # API limit is 360 req/min


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ThreatDown OneView - Bulk Endpoint Delete")
        self.geometry("820x760")
        self.minsize(700, 600)

        config_error = None
        try:
            self.settings = Config.load(CONFIG_PATH)
        except (OSError, ValueError, TypeError) as exc:
            self.settings = Config()
            config_error = f"Could not read {CONFIG_PATH.name}: {exc}"
        self.api = OneViewClient(self.settings.base_url, self.settings.timeout)
        self.sites: list[Site] = []
        self.matched: list[tuple[str, Endpoint]] = []
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self._build_ui()
        self.after(100, self._drain_queue)
        if config_error:
            self.say(config_error)

    def _build_ui(self) -> None:
        pad: dict[str, Any] = {"padx": 8, "pady": 4}

        cred = ttk.LabelFrame(self, text="1. API credentials (OneView > Integrate)")
        cred.pack(fill="x", **pad)
        cred.columnconfigure(1, weight=1)
        ttk.Label(cred, text="Client ID").grid(row=0, column=0, sticky="w", **pad)
        self.client_id = ttk.Entry(cred)
        self.client_id.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(cred, text="Client Secret").grid(row=1, column=0, sticky="w", **pad)
        self.client_secret = ttk.Entry(cred, show="*")
        self.client_secret.grid(row=1, column=1, sticky="ew", **pad)
        self.client_id.insert(0, self.settings.client_id)
        self.client_secret.insert(0, self.settings.client_secret)
        self.remember = tk.BooleanVar(value=bool(self.settings.client_id))
        ttk.Checkbutton(cred, text=f"Remember credentials (saved to {CONFIG_PATH.name})",
                        variable=self.remember).grid(row=2, column=1, sticky="w", **pad)
        self.connect_btn = ttk.Button(cred, text="Connect", command=self.on_connect)
        self.connect_btn.grid(row=0, column=2, rowspan=2, **pad)

        site = ttk.LabelFrame(self, text="2. Select site")
        site.pack(fill="x", **pad)
        site.columnconfigure(0, weight=1)
        self.site_var = tk.StringVar()
        self.site_combo = ttk.Combobox(site, textvariable=self.site_var, state="disabled")
        self.site_combo.grid(row=0, column=0, sticky="ew", **pad)
        self.site_combo.bind("<<ComboboxSelected>>", lambda _e: self._reset_matches())

        names = ttk.LabelFrame(
            self, text="3. Paste computer names (one per line, or comma/space separated)")
        names.pack(fill="both", expand=True, **pad)
        self.names_text = ScrolledText(names, height=10, wrap="word")
        self.names_text.pack(fill="both", expand=True, **pad)
        self.names_text.bind("<<Modified>>", self._on_names_modified)

        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        self.lookup_btn = ttk.Button(actions, text="4. Look up", command=self.on_lookup,
                                     state="disabled")
        self.lookup_btn.pack(side="left", **pad)
        self.dry_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(actions, text="Dry run (don't actually delete)",
                        variable=self.dry_run).pack(side="left", padx=16)
        self.delete_btn = ttk.Button(actions, text="5. Delete matched",
                                     command=self.on_delete, state="disabled")
        self.delete_btn.pack(side="right", **pad)

        self.status_var = tk.StringVar(value="Not connected.")
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", **pad)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = ScrolledText(logf, height=12, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, **pad)

    # Worker threads must not touch Tk directly; they queue callbacks instead.
    def _drain_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def call_ui(self, fn: Callable[[], None]) -> None:
        self.ui_queue.put(fn)

    def say(self, text: str) -> None:
        def append() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.call_ui(append)

    def status(self, text: str) -> None:
        self.call_ui(lambda: self.status_var.set(text))

    def enable(self, button: ttk.Button) -> None:
        def apply() -> None:
            button.configure(state="normal")
        self.call_ui(apply)

    @staticmethod
    def _run_bg(fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _selected_site(self) -> Site | None:
        name = self.site_var.get()
        return next((s for s in self.sites if s.name == name), None)

    def _reset_matches(self) -> None:
        self.matched = []
        self.delete_btn.configure(state="disabled")

    def _on_names_modified(self, _event: tk.Event[Any]) -> None:
        if self.names_text.edit_modified():
            self._reset_matches()
            self.names_text.edit_modified(False)

    def _save_credentials(self, client_id: str, client_secret: str) -> None:
        if not self.remember.get():
            client_id = client_secret = ""
        new_config = replace(self.settings, client_id=client_id, client_secret=client_secret)
        if new_config == self.settings:
            return
        try:
            new_config.save(CONFIG_PATH)
        except OSError as exc:
            self.say(f"Could not save {CONFIG_PATH.name}: {exc}")
            return
        self.settings = new_config
        self.say(f"{'Saved' if client_id else 'Cleared'} credentials in {CONFIG_PATH}")

    def on_connect(self) -> None:
        cid = self.client_id.get().strip()
        secret = self.client_secret.get().strip()
        if not cid or not secret:
            messagebox.showwarning("Missing info", "Enter the client ID and secret.")
            return
        self.connect_btn.configure(state="disabled")
        self.status("Connecting...")

        def work() -> None:
            try:
                self.api.authenticate(cid, secret)
                sites = self.api.get_sites()
            except Exception as exc:
                self.say(f"Connection failed: {exc}")
                self.status("Connection failed.")
                self.enable(self.connect_btn)
                return

            def update() -> None:
                self.sites = sites
                self.site_combo.configure(values=[s.name for s in sites], state="readonly")
                if sites:
                    self.site_combo.current(0)
                self.lookup_btn.configure(state="normal")
                self.connect_btn.configure(state="normal")
                self._save_credentials(cid, secret)
            self.call_ui(update)
            self.say(f"Connected. {len(sites)} site(s) loaded.")
            self.status("Connected. Choose a site and paste computer names.")
        self._run_bg(work)

    def on_lookup(self) -> None:
        site = self._selected_site()
        names = parse_names(self.names_text.get("1.0", "end"))
        if not site:
            messagebox.showwarning("No site", "Select a site first.")
            return
        if not names:
            messagebox.showwarning("No names", "Paste at least one computer name.")
            return
        self.lookup_btn.configure(state="disabled")
        self._reset_matches()
        self.status(f"Fetching endpoints for {site.name}...")

        def work() -> None:
            try:
                endpoints = self.api.get_endpoints(site.account_id)
                result = match_names(names, endpoints)
            except Exception as exc:
                self.say(f"Lookup failed: {exc}")
                self.status("Lookup failed.")
                self.enable(self.lookup_btn)
                return

            self.say(f"--- Lookup in '{site.name}' ({len(endpoints)} endpoints in site) ---")
            self.say(f"Matched: {len(result.matched)}")
            for typed, ep in result.matched:
                self.say(f"  OK    {typed}  ->  {endpoint_label(ep)}  [{endpoint_id(ep)}]")
            if result.ambiguous:
                self.say("Ambiguous (multiple endpoints share the name, SKIPPED): "
                         f"{len(result.ambiguous)}")
                for typed, eps in result.ambiguous:
                    ids = ", ".join(str(endpoint_id(e)) for e in eps)
                    self.say(f"  ??    {typed}  ->  {ids}")
            if result.not_found:
                self.say(f"Not found: {len(result.not_found)}")
                for n in result.not_found:
                    self.say(f"  --    {n}")
            self.status(f"{len(result.matched)} matched, {len(result.ambiguous)} "
                        f"ambiguous, {len(result.not_found)} not found.")

            def finish() -> None:
                self.matched = result.matched
                self.lookup_btn.configure(state="normal")
                if result.matched:
                    self.delete_btn.configure(state="normal")
            self.call_ui(finish)
        self._run_bg(work)

    def on_delete(self) -> None:
        site = self._selected_site()
        if not site or not self.matched:
            return
        dry = self.dry_run.get()
        targets = list(self.matched)
        count = len(targets)

        if dry:
            prompt = f"DRY RUN: simulate deleting {count} endpoint(s) from '{site.name}'?"
        else:
            prompt = (f"PERMANENTLY delete {count} endpoint(s) from '{site.name}'?\n\n"
                      "The agent will be flagged for uninstall. This cannot be undone.")
        if not messagebox.askyesno("Confirm", prompt, icon="warning"):
            return

        self.delete_btn.configure(state="disabled")
        self.lookup_btn.configure(state="disabled")

        def work() -> None:
            ok = fail = 0
            self.say(f"--- {'DRY RUN' if dry else 'DELETING'} {count} endpoint(s) ---")
            for i, (typed, ep) in enumerate(targets, 1):
                eid = endpoint_id(ep)
                assert eid is not None  # match_names drops endpoints without an id
                self.status(f"{'Simulating' if dry else 'Deleting'} {i}/{count}: {typed}")
                if dry:
                    self.say(f"  [dry run] would delete {typed} [{eid}]")
                    ok += 1
                    continue
                try:
                    self.api.delete_endpoint(site.account_id, eid, UNREGISTER_ISOLATED)
                except Exception as exc:
                    fail += 1
                    self.say(f"  FAILED   {typed} [{eid}] ({exc})")
                else:
                    ok += 1
                    self.say(f"  DELETED  {typed} [{eid}]")
                time.sleep(DELETE_DELAY_SEC)
            self.say(f"--- Done: {ok} {'simulated' if dry else 'deleted'}, {fail} failed ---")
            self.status(f"Done. {ok} ok, {fail} failed.")

            def finish() -> None:
                self.lookup_btn.configure(state="normal")
                if dry and self.matched:
                    self.delete_btn.configure(state="normal")
                else:
                    self.matched = []
            self.call_ui(finish)
        self._run_bg(work)


if __name__ == "__main__":
    App().mainloop()
