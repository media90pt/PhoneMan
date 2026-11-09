#!/usr/bin/env python3
"""PhoneMan Linux (protótipo) - backup de contactos, chamadas e SMS via adb.

Requisitos (Debian):
    sudo apt install adb python3-tk libnotify-bin
Uso:
    python3 phoneman.py

    Comando	O que faz	Depende de?

adb devices	Lista dispositivos	Só o adb no PC
adb shell content query --uri ...	Lê content providers	Só o adb + Android
content://com.android.contacts/...	Contactos	Presente em todos os Androids
content://call_log/calls	Histórico de chamadas	Presente em todos os Androids
content://sms	Mensagens SMS	Presente em todos os Androids

Telemóvel: depuração USB ativa e autorizada neste PC.
"""
import csv
import hashlib
import queue
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

# ---------- constantes ----------
SOURCES = {
    "contactos": ("content://com.android.contacts/data/phones", ["display_name", "data1"]),
    "chamadas": ("content://call_log/calls", ["number", "date", "duration", "type", "name"]),
    "sms": ("content://sms", ["address", "date", "type", "body"]),
}
CALL_TYPES = {"1": "recebida", "2": "efetuada", "3": "perdida",
              "4": "voicemail", "5": "rejeitada", "6": "bloqueada"}
SMS_TYPES = {"1": "recebida", "2": "enviada"}


# ---------- notificações ----------
def notify(title, body, urgency="normal", icon="dialog-information"):
    """Envia notificação desktop via notify-send (silencioso se indisponível)."""
    try:
        subprocess.Popen([
            "notify-send",
            "--app-name=PhoneMan",
            f"--urgency={urgency}",
            f"--icon={icon}",
            title,
            body,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError):
        pass


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
    """Devolve lista de (serial, estado)."""
    lines = adb("devices").splitlines()[1:]
    result = []
    for l in lines:
        if not l.strip():
            continue
        parts = l.split("\t")
        if len(parts) >= 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result


def device_connected():
    """Devolve o serial do primeiro dispositivo em estado 'device' ou None."""
    for serial, state in devices():
        if state == "device":
            return serial
    return None


def parse_rows(out, cols):
    """Converte a saída de 'content query' em dicts.
    Robusto a valores com vírgulas ou newlines (ex: corpo de SMS)."""
    rows, skipped = [], 0
    chunks = re.split(r"(?m)^Row: \d+ ", out)[1:]
    for chunk in chunks:
        chunk = chunk.rstrip("\n")
        parts = chunk.split(", ")
        if len(parts) < len(cols):
            skipped += 1
            continue
        if len(parts) > len(cols):
            parts = parts[:len(cols) - 1] + [", ".join(parts[len(cols) - 1:])]
        rows.append({
            c: ("" if v == "NULL" else v)
            for c, v in zip(cols, parts)
        })
    return rows, skipped


def query(key):
    uri, cols = SOURCES[key]
    out = adb("shell", "content", "query", "--uri", uri, "--projection", ":".join(cols))
    head = out[:300]
    if "No result found" in head:
        return [], 0
    if out.startswith(("Error", "[ERROR]")) or "Exception" in head:
        first_line = out.strip().splitlines()[0] if out.strip() else "erro desconhecido"
        raise RuntimeError(first_line)
    return parse_rows(out, cols)


# ---------- exportação ----------
def fmt_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).isoformat(" ", "seconds")
    except (ValueError, OSError, TypeError):
        return str(ms)


def vcard_escape(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def vcard_uid(name, phone):
    return hashlib.md5(f"{name}:{phone}".encode()).hexdigest()


def export(key, rows, folder, ts):
    """Exporta para folder com nome {key}{ts}.{ext} (flat, sem subpastas)."""
    if key == "contactos":
        people = {}
        for r in rows:
            nums = people.setdefault(r["display_name"], [])
            if r["data1"] and r["data1"] not in nums:
                nums.append(r["data1"])
        path = folder / f"contacts{ts}.vcf"
        with open(path, "w", encoding="utf-8") as f:
            for name, nums in people.items():
                uid = vcard_uid(name, nums[0] if nums else "")
                f.write(f"BEGIN:VCARD\nVERSION:3.0\n")
                f.write(f"UID:{uid}\n")
                f.write(f"FN:{vcard_escape(name)}\n")
                f.write(f"N:;{vcard_escape(name)};;;\n")
                for n in nums:
                    f.write(f"TEL;TYPE=CELL:{n}\n")
                f.write("END:VCARD\n")
        return path

    types = CALL_TYPES if key == "chamadas" else SMS_TYPES
    path = folder / f"{key}{ts}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else SOURCES[key][1]
        w = csv.DictWriter(f, fieldnames=fieldnames)
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
        self.geometry("820x470")
        self.q = queue.Queue()
        self.dest = tk.StringVar(value=str(Path.home() / "PhoneMan/Backup"))
        self.status = tk.StringVar(value="A procurar telemóvel...")
        self.last_files = {}

        # ← ícone aqui
        try:
            icon = tk.PhotoImage(file=str(Path(__file__).parent / "icon.png"))
            self.iconphoto(True, icon)
        except tk.TclError:
            pass


        # --- topo ---
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.status).pack(side="left")
        ttk.Button(top, text="Atualizar", command=self.refresh).pack(side="right")

        row = ttk.Frame(self, padding=(8, 0))
        row.pack(fill="x")
        ttk.Label(row, text="Pasta:").pack(side="left")
        ttk.Entry(row, textvariable=self.dest).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="...", width=3, command=self.choose).pack(side="left")

        # --- corpo: 2 colunas ---
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        # ESQUERDA: ações
        left = ttk.LabelFrame(body, text="Ações", padding=8)
        body.add(left, weight=1)
        self.buttons = []
        for label, keys in [("Contactos", ["contactos"]),
                            ("Chamadas", ["chamadas"]),
                            ("SMS", ["sms"]),
                            ("Backup completo", list(SOURCES))]:
            b = ttk.Button(left, text=label, command=lambda k=keys: self.run(k))
            b.pack(fill="x", pady=3)
            self.buttons.append(b)

        # DIREITA: ficheiros + log
        right = ttk.Frame(body)
        body.add(right, weight=3)

        files_frame = ttk.LabelFrame(right, text="Último backup", padding=6)
        files_frame.pack(fill="x")
        self.file_buttons = {}
        for label, key in [("Contactos (.vcf)", "contactos"),
                           ("Chamadas (.csv)", "chamadas"),
                           ("SMS (.csv)", "sms")]:
            b = ttk.Button(files_frame, text=label, state="disabled",
                           command=lambda k=key: self.open_file(k))
            b.pack(fill="x", pady=2)
            self.file_buttons[key] = b

        self.log = scrolledtext.ScrolledText(right, state="disabled", height=12)
        self.log.pack(fill="both", expand=True, pady=(6, 0))

        self.after(100, self.poll)
        self.refresh()

    # --- helpers ---
    def choose(self):
        d = filedialog.askdirectory(initialdir=self.dest.get())
        if d:
            self.dest.set(d)

    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_file(self, key):
        path = self.last_files.get(key)
        if not path or not path.exists():
            self.say(f"Nenhum ficheiro para '{key}' ainda.")
            return
        subprocess.Popen(["xdg-open", str(path)])
        self.say(f"Abrir: {path.name}")

    def _update_file_buttons(self):
        for key, b in self.file_buttons.items():
            if key in self.last_files and self.last_files[key].exists():
                b.state(["!disabled"])
            else:
                b.state(["disabled"])

    # --- polling ---
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
            elif kind == "files":
                self._update_file_buttons()
        self.after(100, self.poll)

    # --- ações ---
    def refresh(self):
        def job():
            try:
                devs = devices()
                connected = [s for s, st in devs if st == "device"]
                if connected:
                    self.q.put(("status", f"Telemóvel: {connected[0]}"))
                else:
                    states = {st for _, st in devs}
                    if "unauthorized" in states:
                        self.q.put(("status", "Telemóvel não autorizado (aceita no ecrã)"))
                    elif "offline" in states:
                        self.q.put(("status", "Telemóvel offline"))
                    else:
                        self.q.put(("status", "Nenhum telemóvel ligado"))
            except Exception as e:
                self.q.put(("status", f"Erro: {e}"))
        threading.Thread(target=job, daemon=True).start()

    def run(self, keys):
        threading.Thread(target=self.work, args=(keys,), daemon=True).start()

    def work(self, keys):
        self.q.put(("busy", True))
        try:
            serial = device_connected()
            if not serial:
                self.q.put(("log", "Nenhum telemóvel ligado/autorizado."))
                notify("PhoneMan", "Nenhum telemóvel ligado.", "critical", "dialog-error")
                return
            folder = Path(self.dest.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.q.put(("log", f"Backup: {folder} [{ts}]"))
            total = 0
            errors = 0
            for k in keys:
                try:
                    self.q.put(("log", f"  A ler {k}..."))
                    rows, skipped = query(k)
                    path = export(k, rows, folder, ts)
                    self.last_files[k] = path
                    total += len(rows)
                    msg = f"  {k}: {len(rows)} registos -> {path.name}"
                    if skipped:
                        msg += f" ({skipped} ignoradas)"
                    self.q.put(("log", msg))
                except Exception as e:
                    errors += 1
                    self.q.put(("log", f"  ERRO em {k}: {e}"))
                    notify("PhoneMan", f"Erro em {k}: {e}", "critical", "dialog-error")
            self.q.put(("log", "✓ Concluído"))
            self.q.put(("files", None))
            if errors == 0:
                notify("PhoneMan", f"Backup concluído: {total} registos",
                       "normal", "dialog-information")
            else:
                notify("PhoneMan", f"Concluído com {errors} erro(s): {total} registos",
                       "normal", "dialog-warning")
        except Exception as e:
            self.q.put(("log", f"ERRO: {e}"))
            notify("PhoneMan", f"Erro: {e}", "critical", "dialog-error")
        finally:
            self.q.put(("busy", False))


if __name__ == "__main__":
    App().mainloop()
