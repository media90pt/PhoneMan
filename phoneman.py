#!/usr/bin/env python3
"""PhoneMan Linux (protótipo) - backup de contactos, chamadas e SMS via adb.

Requisitos (Debian):

sudo apt install adb python3-tk
python3 phoneman.py
adb shell content query --uri content://sms/inbox
adb shell content query --uri content://com.android.contacts/contacts

Testes rápidos para veres o que o adb consegue ler no teu telemóvel:

adb shell content query --uri content://sms/inbox
adb shell content query --uri content://com.android.contacts/contacts


Telemóvel: depuração USB ativa e autorizada neste PC.
Uso:  python3 phoneman.py
"""
import csv
import queue
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

# nome -> (URI do content provider, colunas pedidas)
SOURCES = {
    "contactos": ("content://com.android.contacts/data/phones", ["display_name", "data1"]),
    "chamadas": ("content://call_log/calls", ["number", "date", "duration", "type", "name"]),
    "sms": ("content://sms", ["address", "date", "type", "body"]),
}
CALL_TYPES = {"1": "recebida", "2": "efetuada", "3": "perdida", "4": "voicemail",
              "5": "rejeitada", "6": "bloqueada"}
SMS_TYPES = {"1": "recebida", "2": "enviada"}


# ---------- adb ----------
def adb(*args, timeout=180):
    try:
        r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("adb não encontrado. Instala com: sudo apt install adb")
    out = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or out.strip())
    return out


def devices():
    lines = adb("devices").splitlines()[1:]
    return [l.split("\t")[0] for l in lines if l.strip().endswith("device")]


def parse_rows(out, cols):
    """Converte a saída de 'content query' ('Row: N col=val, col=val') em dicts."""
    pat = re.compile("^" + ", ".join(f"{c}=(.*?)" for c in cols) + "$", re.S)
    rows, skipped = [], 0
    for chunk in re.split(r"(?m)^Row: \d+ ", out)[1:]:
        m = pat.match(chunk.rstrip("\n"))
        if not m:
            skipped += 1
            continue
        rows.append({c: ("" if v == "NULL" else v) for c, v in zip(cols, m.groups())})
    return rows, skipped


def query(key):
    uri, cols = SOURCES[key]
    out = adb("shell", "content", "query", "--uri", uri, "--projection", ":".join(cols))
    head = out[:300]
    if "No result found" in head:
        return [], 0
    if out.startswith(("Error", "[ERROR]")) or "Exception" in head:
        raise RuntimeError(head.strip().splitlines()[0] if head.strip() else "erro desconhecido")
    return parse_rows(out, cols)


# ---------- exportação ----------
# -------------------------------------
    try:
        return datetime.fromtimestamp(int(ms) / 1000).isoformat(" ", "seconds")
    except (ValueError, OSError):
        return ms


def vcard_escape(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def export(key, rows, folder):
    if key == "contactos":
        people = {}
        for r in rows:
            nums = people.setdefault(r["display_name"], [])
            if r["data1"] and r["data1"] not in nums:
                nums.append(r["data1"])
        path = folder / "contactos.vcf"
        with open(path, "w", encoding="utf-8") as f:
            for name, nums in people.items():
                f.write(f"BEGIN:VCARD\nVERSION:3.0\nFN:{vcard_escape(name)}\n"
                        f"N:;{vcard_escape(name)};;;\n")
                for n in nums:
                    f.write(f"TEL;TYPE=CELL:{n}\n")
                f.write("END:VCARD\n")
        return path

    types = CALL_TYPES if key == "chamadas" else SMS_TYPES
    path = folder / f"{key}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else SOURCES[key][1])
        w.writeheader()
        for r in rows:
            r = dict(r, date=fmt_date(r["date"]), type=types.get(r["type"], r["type"]))
            w.writerow(r)
    return path


# ---------- interface ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PhoneMan Linux")
        self.geometry("660x470")
        self.q = queue.Queue()
        self.dest = tk.StringVar(value=str(Path.home() / "PhoneMan/Backups"))
        self.status = tk.StringVar(value="A procurar telemóvel...")

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.status).pack(side="left")
        ttk.Button(top, text="Atualizar", command=self.refresh).pack(side="right")

        row = ttk.Frame(self, padding=(8, 0))
        row.pack(fill="x")
        ttk.Label(row, text="Pasta:").pack(side="left")
        ttk.Entry(row, textvariable=self.dest).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="...", width=3, command=self.choose).pack(side="left")

        btns = ttk.Frame(self, padding=8)
        btns.pack(fill="x")
        self.buttons = []
        for label, keys in [("Contactos", ["contactos"]), ("Chamadas", ["chamadas"]),
                            ("SMS", ["sms"]), ("Backup completo", list(SOURCES))]:
            b = ttk.Button(btns, text=label, command=lambda k=keys: self.run(k))
            b.pack(side="left", padx=2)
            self.buttons.append(b)

        self.log = scrolledtext.ScrolledText(self, state="disabled", height=16)
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.after(100, self.poll)
        self.refresh()

    def choose(self):
        d = filedialog.askdirectory(initialdir=self.dest.get())
        if d:
            self.dest.set(d)

    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        while not self.q.empty():
            kind, val = self.q.get()
            if kind == "log":
                self.say(val)
            elif kind == "status":
                self.status.set(val)
            elif kind == "busy":
                for b in self.buttons:
                    b.state(["disabled"] if val else ["!disabled"])
        self.after(100, self.poll)

    def refresh(self):
        def job():
            try:
                devs = devices()
                self.q.put(("status", f"Telemóvel ligado: {devs[0]}" if devs
                            else "Nenhum telemóvel autorizado (verifica a depuração USB)"))
            except Exception as e:
                self.q.put(("status", f"Erro: {e}"))
        threading.Thread(target=job, daemon=True).start()

    def run(self, keys):
        threading.Thread(target=self.work, args=(keys,), daemon=True).start()

    def work(self, keys):
        self.q.put(("busy", True))
        try:
            if not devices():
                self.q.put(("log", "Nenhum telemóvel ligado/autorizado."))
                return
            folder = Path(self.dest.get()).expanduser() / datetime.now().strftime("backup-%Y%m%d-%H%M%S")
            folder.mkdir(parents=True, exist_ok=True)
            for k in keys:
                try:
                    self.q.put(("log", f"A ler {k}..."))
                    rows, skipped = query(k)
                    path = export(k, rows, folder)
                    msg = f"  {k}: {len(rows)} registos -> {path.name}"
                    if skipped:
                        msg += f" ({skipped} linhas ignoradas)"
                    self.q.put(("log", msg))
                except Exception as e:
                    self.q.put(("log", f"  ERRO em {k}: {e}"))
            self.q.put(("log", f"Pasta: {folder}"))
        except Exception as e:
            self.q.put(("log", f"ERRO: {e}"))
        finally:
            self.q.put(("busy", False))


if __name__ == "__main__":
    App().mainloop()
