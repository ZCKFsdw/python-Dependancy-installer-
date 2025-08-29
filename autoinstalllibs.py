import customtkinter as ctk
from tkinter import filedialog, simpledialog
from tkinterdnd2 import DND_FILES, TkinterDnD
import subprocess
import re
import os
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from queue import Queue

# ============== إعداد الثيم ==============
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ============== نافذة البرنامج ==============
app = TkinterDnD.Tk()
app.geometry("900x700")
app.title("📦 Python Dependency Installer — Smart Update Edition")

# حالة عامة
last_packages = []
MAX_THREADS = 6
outdated_cache = []         # يُملأ عند فحص التحديثات
log_queue = Queue()
timer_start = None

# ============== مساعدات واجهة ==============
def set_status(text):
    status.configure(text=text)
    app.update_idletasks()

def start_timer(label=""):
    global timer_start
    timer_start = time.time()
    if label:
        set_status(label)

def stop_timer(prefix="تم"):
    if timer_start is None:
        return
    elapsed = time.time() - timer_start
    set_status(f"{prefix} • الزمن: {elapsed:.1f}s")

def set_progress_mode(mode="idle"):
    # تغيير لون شريط التقدم حسب العملية
    colors = {
        "install": "#22c55e",  # أخضر
        "update":  "#3b82f6",  # أزرق
        "shell":   "#a855f7",  # بنفسجي
        "idle":    "#6b7280",  # رمادي
    }
    progress.configure(progress_color=colors.get(mode, "#6b7280"))
    app.update_idletasks()

def log_configure_tags():
    # CTkTextbox يرث من tk.Text، لذلك يدعم tags
    try:
        log.tag_config("info", foreground="#cbd5e1")
        log.tag_config("ok", foreground="#22c55e")
        log.tag_config("warn", foreground="#f59e0b")
        log.tag_config("err", foreground="#ef4444")
        log.tag_config("title", foreground="#93c5fd")
    except Exception:
        pass

def log_write(text, level="info"):
    log.insert("end", text + "\n", level)
    log.see("end")
    app.update_idletasks()

# ============== أدوات استخراج المكتبات ==============
def extract_from_py(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    imports = re.findall(r'^\s*(?:import|from)\s+([a-zA-Z0-9_]+)', content, re.MULTILINE)
    return sorted(set(imports))

def extract_from_txt(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    packages = [line.strip() for line in lines if line.strip() and not line.strip().startswith('#')]
    return packages

def is_installed(pkg_name):
    if "==" in pkg_name:
        pkg_name = pkg_name.split("==")[0]
    return importlib.util.find_spec(pkg_name) is not None

# ============== تثبيت مكتبة واحدة ==============
def install_package(pkg):
    try:
        if "--upgrade" in pkg:
            base = pkg.replace(" --upgrade", "")
            log_queue.put(("info", f"⏫ Upgrading: {base}"))
            subprocess.check_call(['pip', 'install', '--upgrade', base])
            log_queue.put(("ok", f"✅ Upgraded: {base}"))
        else:
            if is_installed(pkg):
                log_queue.put(("info", f"⚡ موجود مسبقاً: {pkg}"))
            else:
                log_queue.put(("info", f"⬇️ Installing: {pkg}"))
                subprocess.check_call(['pip', 'install', pkg])
                log_queue.put(("ok", f"✅ Installed: {pkg}"))
    except subprocess.CalledProcessError:
        log_queue.put(("err", f"❌ Failed: {pkg}"))

# ============== تنفيذ سكريبت .sh (متبقي من الأساس) ==============
def run_sh(file_path):
    set_progress_mode("shell")
    start_timer("تشغيل سكريبت شِل...")
    try:
        process = subprocess.Popen(
            ['bash', file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in process.stdout:
            log_write(line.rstrip("\n"), "info")
        process.wait()
        log_write("🎉 تم الانتهاء من تنفيذ السكريبت!", "ok")
    except Exception as e:
        log_write(f"❌ خطأ أثناء تنفيذ السكريبت: {e}", "err")
    finally:
        set_progress_mode("idle")
        stop_timer("انتهى تنفيذ الشِل")

# ============== تثبيت/تحديث مجموعة مكتبات بمتوازٍ ==============
def threaded_run(packages, mode="install"):
    if not packages:
        return
    # إعداد UI
    set_progress_mode("install" if mode == "install" else "update")
    progress.set(0)
    total = len(packages)
    done = 0
    start_timer("بدء العملية..." if mode == "install" else "بدء التحديث...")

    def pump_log():
        # سحب الرسائل من الطابور وعرضها
        nonlocal done
        while not log_queue.empty():
            level, msg = log_queue.get()
            log_write(msg, level)
            if msg.startswith(("✅", "❌", "⚡")):
                done += 1
                progress.set(min(1.0, done / total))
        if done < total:
            app.after(120, pump_log)
        else:
            # ختام
            if mode == "install":
                log_write("🎉 تم الانتهاء من التثبيت.", "ok")
                stop_timer("اكتمل التثبيت")
            else:
                log_write("🎉 تم الانتهاء من التحديث.", "ok")
                stop_timer("اكتمل التحديث")
            set_progress_mode("idle")

    executor = ThreadPoolExecutor(max_workers=MAX_THREADS)
    for pkg in packages:
        executor.submit(install_package, pkg)
    # لا ننتظر هنا بشكل متزامن؛ نعتمد على المضخة
    app.after(120, pump_log)

# ============== تحديث مكتبات ذكي ==============
def pip_outdated():
    """يرجع قائمة بالقواميس: name, current_version, latest_version"""
    try:
        # نحاول JSON أولاً (أسرع وأدق)
        res = subprocess.run(
            ['pip', 'list', '--outdated', '--format', 'json'],
            capture_output=True, text=True, check=True
        )
        data = json.loads(res.stdout.strip() or "[]")
        results = []
        for item in data:
            # مفاتيح شائعة: name, version, latest_version
            results.append({
                "name": item.get("name"),
                "current": item.get("version"),
                "latest": item.get("latest_version") or item.get("latest")
            })
        return results
    except Exception:
        # احتياطي: تحليل نصي (لو تعطل --format=json)
        res = subprocess.run(['pip', 'list', '--outdated'], capture_output=True, text=True)
        lines = res.stdout.splitlines()[2:]  # تخطي العناوين
        results = []
        for ln in lines:
            parts = ln.split()
            if len(parts) >= 3:
                results.append({"name": parts[0], "current": parts[1], "latest": parts[2]})
        return results

def check_updates():
    set_progress_mode("update")
    log_write("🔎 فحص المكتبات القديمة...", "title")
    start_timer("فحص التحديثات...")
    def task():
        global outdated_cache
        try:
            items = pip_outdated()
            outdated_cache = items
            if not items:
                log_write("✔️ لا توجد مكتبات قديمة. كل شيء محدث! 👌", "ok")
                return
            log_write(f"📋 مكتبات قديمة ({len(items)}):", "warn")
            for it in items:
                log_write(f"- {it['name']}: {it['current']} → {it['latest']}", "info")
            log_write("يمكنك الضغط على زر Update Outdated لتحديثها كلها.", "warn")
        except Exception as e:
            log_write(f"❌ فشل فحص التحديثات: {e}", "err")
        finally:
            set_progress_mode("idle")
            stop_timer("انتهى الفحص")
    threading.Thread(target=task, daemon=True).start()

def update_outdated():
    if not outdated_cache:
        log_write("ℹ️ لا توجد نتيجة فحص محفوظة. اضغط 'Check Updates' أولاً.", "warn")
        return
    pkgs = [f"{x['name']} --upgrade" for x in outdated_cache if x.get("name")]
    log_write(f"⏫ بدء تحديث {len(pkgs)} مكتبة قديمة...", "title")
    threaded_run(pkgs, mode="update")

# ============== Traceback Fixer (متقدم) ==============
def traceback_fixer():
    error_text = simpledialog.askstring("Traceback Fixer", "📝 ألصق رسالة الخطأ هنا:")
    if not error_text:
        return
    patterns = [
        r"No module named '([^']+)'",
        r'No module named "([^"]+)"',
        r"ImportError: No module named '([^']+)'",
        r"ModuleNotFoundError: No module named '([^']+)'",
    ]
    missing = []
    for pat in patterns:
        missing += re.findall(pat, error_text)
    missing = sorted(set(missing))
    if missing:
        log_write("🐍 المكتبات الناقصة المكتشفة:", "warn")
        for m in missing:
            log_write(f"- {m}", "info")
        threaded_run(missing, mode="install")
    else:
        log_write("⚠️ لم أتمكن من تحديد المكتبة من الخطأ.", "warn")

# ============== التعامل مع الملفات (اختياري؛ للحفاظ على الأساس) ==============
def process_file(file_path):
    global last_packages
    log_write(f"📄 Selected: {file_path}", "title")
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".py":
        pkgs = extract_from_py(file_path)
        last_packages = pkgs
        if pkgs:
            log_write(f"🔍 Found: {', '.join(pkgs)}", "info")
            set_progress_mode("install")
            threaded_run(pkgs, mode="install")
        else:
            log_write("ℹ️ لم يتم العثور على استيرادات واضحة.", "warn")
    elif ext == ".txt":
        pkgs = extract_from_txt(file_path)
        last_packages = pkgs
        if pkgs:
            log_write(f"🔍 Found: {', '.join(pkgs)}", "info")
            set_progress_mode("install")
            threaded_run(pkgs, mode="install")
        else:
            log_write("ℹ️ الملف فارغ.", "warn")
    elif ext == ".sh":
        log_write("🐚 تنفيذ سكريبت Shell...", "info")
        threading.Thread(target=lambda: run_sh(file_path), daemon=True).start()
    else:
        log_write("⚠️ نوع ملف غير مدعوم. استخدم .py / .txt / .sh", "warn")

def choose_file():
    file_path = filedialog.askopenfilename(
        filetypes=[("Supported Files", "*.py *.txt *.sh"), ("All Files", "*.*")]
    )
    if file_path:
        process_file(file_path)

# ============== Drag & Drop محسن (ألوان ديناميكية) ==============
def drop(event):
    files = app.tk.splitlist(event.data)
    for fp in files:
        process_file(fp)
    frame.configure(fg_color="#2b2f36")  # رجوع اللون الطبيعي

def drag_enter(event):
    files = app.tk.splitlist(event.data)
    if not files:
        return
    ext = os.path.splitext(files[0])[1].lower()
    if ext == ".py":
        frame.configure(fg_color="#1f3d2b")  # أخضر داكن
    elif ext == ".txt":
        frame.configure(fg_color="#102a43")  # أزرق داكن
    elif ext == ".sh":
        frame.configure(fg_color="#2a1f3d")  # بنفسجي داكن
    else:
        frame.configure(fg_color="#3b3b3b")

def drag_leave(event):
    frame.configure(fg_color="#2b2f36")

# ============== تثبيت يدوي (يبقى مفيد) ==============
def manual_install():
    pkg = entry.get().strip()
    if not pkg:
        return
    log_write(f"🔎 Trying to install: {pkg}", "info")
    set_progress_mode("install")
    threaded_run([pkg], mode="install")

# ============== الواجهة ==============
# رأس
header = ctk.CTkFrame(app, corner_radius=14)
header.pack(fill="x", pady=10, padx=12)

title_label = ctk.CTkLabel(header, text="📦 Python Dependency Installer", font=("Arial", 26, "bold"))
title_label.pack(pady=12)

# إطار رئيسي
frame = ctk.CTkFrame(master=app, corner_radius=16, fg_color="#2b2f36")
frame.pack(pady=10, padx=16, fill="both", expand=True)

# صفّ إدخال يدوي
entry = ctk.CTkEntry(master=frame, placeholder_text="🔹 اكتب اسم المكتبة (مثال: requests==2.32.3)", width=520, height=40)
entry.grid(row=0, column=0, padx=10, pady=12, sticky="w")

manual_btn = ctk.CTkButton(master=frame, text="➕ تثبيت", fg_color="#22c55e", width=160, height=40, command=manual_install)
manual_btn.grid(row=0, column=1, padx=10, pady=12)

# صفّ اختيار ملف + أزرار التحديث الذكي
choose_btn = ctk.CTkButton(master=frame, text="📂 اختر ملف", fg_color="#1e90ff", width=180, height=40, command=choose_file)
choose_btn.grid(row=1, column=0, padx=10, pady=8, sticky="w")

check_updates_btn = ctk.CTkButton(master=frame, text="🔎 Check Updates", fg_color="#3b82f6", width=180, height=40, command=check_updates)
check_updates_btn.grid(row=1, column=1, padx=10, pady=8, sticky="e")

update_outdated_btn = ctk.CTkButton(master=frame, text="⏫ Update Outdated", fg_color="#2563eb", width=180, height=40, command=update_outdated)
update_outdated_btn.grid(row=2, column=1, padx=10, pady=6, sticky="e")

traceback_btn = ctk.CTkButton(master=frame, text="🛠️ Traceback Fixer", fg_color="#8a2be2", width=180, height=40, command=traceback_fixer)
traceback_btn.grid(row=2, column=0, padx=10, pady=6, sticky="w")

# شريط التقدم
progress = ctk.CTkProgressBar(master=frame, width=700, height=20)
progress.set(0)
progress.grid(row=3, column=0, columnspan=2, pady=12)

# اللوج
log = ctk.CTkTextbox(master=frame, width=820, height=360, corner_radius=12)
log.grid(row=4, column=0, columnspan=2, padx=10, pady=10)
log_configure_tags()

# شريط الحالة السفلي
status_bar = ctk.CTkFrame(app, corner_radius=0)
status_bar.pack(fill="x", side="bottom")
status = ctk.CTkLabel(status_bar, text="جاهز.", anchor="w", padx=12)
status.pack(fill="x")

# تفعيل سحب/إفلات
frame.drop_target_register(DND_FILES)
frame.dnd_bind('<<DragEnter>>', drag_enter)
frame.dnd_bind('<<DragLeave>>', drag_leave)
frame.dnd_bind('<<Drop>>', drop)

# جاهزية
set_progress_mode("idle")
set_status("جاهز.")

app.mainloop()
