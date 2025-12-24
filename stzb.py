import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
os.environ['OMP_NUM_THREADS'] = '1'

import cv2
import numpy as np
import easyocr
import sqlite3
import json
import time
import threading
import difflib
import re
import hashlib
from datetime import datetime
from tkinter import *
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageGrab

# ==================== 1. 配置与规则 ====================
OCR_CORRECTIONS = {
    "璀": "瓘", "藜": "蒙", "柞": "布", "口": "吕", 
    "肾": "晋", "误": "吴", "攸": "彧", "或": "彧",
    "l": "丨", "I": "丨"
}
FACTION_MAP = {"吴": ["吴", "误", "口", "昊"], "汉": ["汉", "议", "汗"], "群": ["群", "郡", "君"], "魏": ["魏", "巍"], "蜀": ["蜀", "属"], "晋": ["晋", "肾"]}

def load_general_pool():
    file_path = "武将列表.txt"
    default_pool = ["大乔", "张机", "孙权", "吕布", "吕蒙", "曹操", "刘备", "关羽", "马超", "卫瓘", "荀彧", "荀攸", "魏延", "妲己", "木鹿大王", "李儒", "夏侯惇", "夏侯霸"]
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f: f.write("\n".join(default_pool))
        return default_pool
    try:
        with open(file_path, "r", encoding="utf-8") as f: return [line.strip() for line in f.readlines() if line.strip()]
    except: return default_pool

GENERAL_POOL = load_general_pool()

# ==================== 2. 数据库管理 ====================
class DatabaseManager:
    def __init__(self):
        self.db_name = "rate_of_land.db"
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS players (name TEXT PRIMARY KEY, last_seen TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS teams (player_name TEXT, team_json TEXT, team_hash TEXT PRIMARY KEY, first_seen TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS trust_list (name TEXT PRIMARY KEY)")
            conn.commit()

    def get_all_player_names(self):
        with sqlite3.connect(self.db_name) as conn:
            return [r[0] for r in conn.execute("SELECT name FROM players ORDER BY last_seen DESC").fetchall()]

    def is_trusted(self, name):
        with sqlite3.connect(self.db_name) as conn:
            return conn.execute("SELECT 1 FROM trust_list WHERE name = ?", (name,)).fetchone() is not None

    def add_to_trust(self, name):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("INSERT OR IGNORE INTO trust_list VALUES (?)", (name,))
            conn.commit()

    def remove_from_trust(self, name):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("DELETE FROM trust_list WHERE name = ?", (name,))
            conn.commit()

    def get_trust_list(self):
        with sqlite3.connect(self.db_name) as conn:
            return [r[0] for r in conn.execute("SELECT name FROM trust_list").fetchall()]

    def rename_player(self, old_name, new_name):
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("UPDATE OR IGNORE teams SET player_name = ? WHERE player_name = ?", (new_name, old_name))
            c.execute("DELETE FROM players WHERE name = ?", (old_name,))
            c.execute("INSERT OR REPLACE INTO players VALUES (?, ?)", (new_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()

    def delete_player(self, name):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("DELETE FROM players WHERE name = ?", (name,))
            conn.execute("DELETE FROM teams WHERE player_name = ?", (name,))
            conn.commit()

    def delete_team(self, team_hash):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("DELETE FROM teams WHERE team_hash = ?", (team_hash,))
            conn.commit()

    def save_record(self, player_name, team_list):
        while len(team_list) < 3: team_list.append("未知 · 未知")
        names_only = [t.split(" · ")[1] if " · " in t else t for t in team_list]
        team_hash = hashlib.md5(f"{player_name}{''.join(names_only)}".encode('utf-8')).hexdigest()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO players VALUES (?, ?)", (player_name, now))
            c.execute("INSERT OR IGNORE INTO teams VALUES (?, ?, ?, ?)", (player_name, json.dumps(team_list, ensure_ascii=False), team_hash, now))
            conn.commit()

# ==================== 3. 弹窗 UI ====================
class SimilarityDialog(Toplevel):
    def __init__(self, parent, new_name, old_name):
        super().__init__(parent)
        self.title("重名检查")
        self.geometry("450x250")
        self.result = None
        # 默认不勾选：value=False
        self.trust_var = BooleanVar(value=False)
        
        Label(self, text="⚠️ 发现高度相似玩家名", font=("微软雅黑", 12, "bold"), fg="#e67e22").pack(pady=10)
        Label(self, text=f"当前识别: {new_name}", font=("微软雅黑", 10)).pack()
        Label(self, text=f"数据库已有: {old_name}", font=("微软雅黑", 10, "bold"), fg="#2980b9").pack()
        
        btn_f = Frame(self)
        btn_f.pack(pady=15)
        ttk.Button(btn_f, text=f"使用新名 (覆盖旧名)", command=lambda: self.done("use_new")).pack(side=LEFT, padx=10)
        ttk.Button(btn_f, text=f"保留旧名 (忽略新名)", command=lambda: self.done("keep_old")).pack(side=LEFT, padx=10)
        
        Checkbutton(self, text="不再询问 (记录此选择并加入信任名单)", variable=self.trust_var).pack()
        self.grab_set()

    def done(self, action):
        self.result = (action, self.trust_var.get())
        self.destroy()

class TrustManager(Toplevel):
    def __init__(self, parent, db, callback):
        super().__init__(parent)
        self.title("白名单管理")
        self.geometry("300x400")
        self.db = db
        self.callback = callback
        
        Label(self, text="已信任的名字列表", font=("微软雅黑", 10, "bold")).pack(pady=5)
        self.listbox = Listbox(self, font=("微软雅黑", 10))
        self.listbox.pack(fill=BOTH, expand=True, padx=10, pady=5)
        
        self.refresh()
        ttk.Button(self, text="移除选中信任", command=self.remove_name).pack(pady=10)

    def refresh(self):
        self.listbox.delete(0, END)
        for name in self.db.get_trust_list():
            self.listbox.insert(END, name)

    def remove_name(self):
        sel = self.listbox.curselection()
        if not sel: return
        name = self.listbox.get(sel[0])
        self.db.remove_from_trust(name)
        self.refresh()

# ==================== 4. 识别引擎 ====================
class RecognitionEngine:
    def __init__(self):
        self.reader = None
        threading.Thread(target=self._init_ocr, daemon=True).start()

    def _init_ocr(self):
        self.reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)

    def _clean_text(self, text):
        for k, v in OCR_CORRECTIONS.items(): text = text.replace(k, v)
        return text

    def has_any_text(self, region):
        if not self.reader or not region: return False
        try:
            img = ImageGrab.grab(bbox=(region[0], region[1], region[0]+region[2], region[1]+region[3]))
            return len(self.reader.readtext(np.array(img))) > 0
        except: return False

    def check_detail_flag(self, region):
        if not self.reader or not region: return False
        try:
            img = ImageGrab.grab(bbox=(region[0], region[1], region[0]+region[2], region[1]+region[3]))
            full_text = "".join([r[1] for r in self.reader.readtext(np.array(img))])
            return any(word in full_text for word in ["战报", "详情", "详", "报详"])
        except: return False

    def recognize(self, region, is_player=False):
        if not self.reader or not region: return "未知"
        try:
            img = ImageGrab.grab(bbox=(region[0], region[1], region[0]+region[2], region[1]+region[3]))
            img_np = cv2.resize(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), (0, 0), fx=2, fy=2)
            results = self.reader.readtext(img_np)
            full_text = self._clean_text("".join([r[1] for r in results]))
            
            if is_player: 
                return re.sub(r'[^\u4e00-\u9fff\w丨]', '', full_text) or "未知玩家"
            
            faction = "未知"
            for f, aliases in FACTION_MAP.items():
                for a in aliases:
                    if a in full_text: faction = f; full_text = full_text.replace(a, ""); break
            name_part = re.sub(r'[^\u4e00-\u9fff]', '', full_text)
            match = difflib.get_close_matches(name_part, GENERAL_POOL, n=1, cutoff=0.3)
            return f"{faction} · {match[0] if match else (name_part or '未知')}"
        except: return "异常"

# ==================== 5. 主程序 ====================
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("率土情报管家 v7.7.3")
        self.root.geometry("1100x750")
        self.db = DatabaseManager()
        self.engine = RecognitionEngine()
        self.config_file = "config.json"
        self.config = {"icon_reg": None, "name_reg": None, "gen_regs": [], "block_reg": None}
        self.is_monitoring = False
        
        self._load_saved_config()
        self._build_ui()
        self.refresh_player_list()

    def _load_saved_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    for k in self.config:
                        if k in saved: self.config[k] = saved[k]
            except: pass

    def _save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False)

    def _build_ui(self):
        top = Frame(self.root, bg="white", height=60)
        top.pack(fill=X); top.pack_propagate(False)
        
        btn_f = Frame(top, bg="white")
        btn_f.pack(side=LEFT, padx=10)
        ttk.Button(btn_f, text="0.框选战报", command=self.set_icon_reg).pack(side=LEFT, padx=2)
        ttk.Button(btn_f, text="1.框选玩家", command=self.set_name_reg).pack(side=LEFT, padx=2)
        ttk.Button(btn_f, text="2.框选武将", command=self.set_gen_regs_auto).pack(side=LEFT, padx=2)
        ttk.Button(btn_f, text="3.框选干扰", command=self.set_block_reg).pack(side=LEFT, padx=2)
        ttk.Button(btn_f, text="🛡 管理信任名单", command=self.open_trust_mgr).pack(side=LEFT, padx=10)
        
        self.status_label = Label(top, text="● 待命", bg="white", font=("微软雅黑", 10), fg="gray")
        self.status_label.pack(side=LEFT, padx=20)
        
        self.btn_run = Button(top, text="▶ 开始监控", bg="#27ae60", fg="white", font=("微软雅黑", 10, "bold"), command=self.toggle, width=12)
        self.btn_run.pack(side=RIGHT, padx=20)

        pw = PanedWindow(self.root, orient=HORIZONTAL, bg="#ddd")
        pw.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        left_container = Frame(pw, bg="white")
        pw.add(left_container, width=300)
        
        search_f = Frame(left_container, bg="#f5f5f5", padx=5, pady=5)
        search_f.pack(fill=X)
        Label(search_f, text="🔍", bg="#f5f5f5").pack(side=LEFT)
        self.search_var = StringVar()
        self.search_var.trace("w", lambda *args: self.refresh_player_list())
        self.search_entry = ttk.Entry(search_f, textvariable=self.search_var)
        self.search_entry.pack(side=LEFT, fill=X, expand=True, padx=2)
        ttk.Button(search_f, text="X", width=2, command=lambda: self.search_var.set("")).pack(side=RIGHT)

        self.player_list = ttk.Treeview(left_container, columns=("name"), show="headings")
        self.player_list.heading("name", text="玩家 ID")
        self.player_list.pack(fill=BOTH, expand=True)
        self.player_list.bind("<<TreeviewSelect>>", self.on_player_select)
        self.player_list.bind("<Button-3>", self.show_player_menu) 
        
        right_f = Frame(pw, bg="white")
        pw.add(right_f)
        self.team_table = ttk.Treeview(right_f, columns=("time", "b", "m", "f", "hash"), show="headings")
        for c, t in zip(["time", "b", "m", "f"], ["录入时间", "大营", "中军", "前锋"]):
            self.team_table.heading(c, text=t); self.team_table.column(c, width=120)
        self.team_table.heading("hash", text="Hash"); self.team_table.column("hash", width=0, stretch=NO)
        self.team_table.pack(fill=BOTH, expand=True)
        self.team_table.bind("<Button-3>", self.show_team_menu)

    def refresh_player_list(self):
        search_term = self.search_var.get().strip().lower()
        self.player_list.delete(*self.player_list.get_children())
        all_names = self.db.get_all_player_names()
        for n in all_names:
            if not search_term or search_term in n.lower():
                self.player_list.insert("", END, values=(n,))

    def toggle(self):
        if not self.config["name_reg"]: return messagebox.showwarning("警告", "请先完成框选")
        self.is_monitoring = not self.is_monitoring
        self.btn_run.config(text="⏹ 停止" if self.is_monitoring else "▶ 开始监控", bg="#c0392b" if self.is_monitoring else "#27ae60")
        if self.is_monitoring: threading.Thread(target=self.monitor_thread, daemon=True).start()

    def monitor_thread(self):
        while self.is_monitoring:
            if self.config["block_reg"] and self.engine.has_any_text(self.config["block_reg"]):
                self.root.after(0, lambda: self.status_label.config(text="● 干扰中(停止识别)", fg="red"))
                time.sleep(1.0); continue

            if self.engine.check_detail_flag(self.config["icon_reg"]):
                p_name = self.engine.recognize(self.config["name_reg"], True)
                if p_name != "未知玩家":
                    # 识别全队武将
                    teams = [self.engine.recognize(r) for r in self.config["gen_regs"]]
                    
                    # --- 已移除大营存在检查 ---
                    final_name = self.handle_name_logic(p_name)
                    self.db.save_record(final_name, teams)
                    self.root.after(0, self.refresh_player_list)
                    self.root.after(0, lambda: self.status_label.config(text=f"● 录入: {final_name}", fg="#27ae60"))
            else:
                self.root.after(0, lambda: self.status_label.config(text="● 等待战报详情页...", fg="#f39c12"))
            time.sleep(1.5)

    def handle_name_logic(self, name):
        if self.db.is_trusted(name): return name
        all_names = self.db.get_all_player_names()
        for old in all_names:
            ratio = difflib.SequenceMatcher(None, name, old).ratio()
            if 0.75 <= ratio < 1.0:
                dialog = SimilarityDialog(self.root, name, old)
                self.root.wait_window(dialog)
                if dialog.result:
                    action, trust = dialog.result
                    if action == "use_new":
                        self.db.rename_player(old, name)
                        if trust: self.db.add_to_trust(name)
                        return name
                    else:
                        if trust: self.db.add_to_trust(old)
                        return old
                break
        return name

    def show_player_menu(self, event):
        item = self.player_list.identify_row(event.y)
        if item:
            self.player_list.selection_set(item)
            name = self.player_list.item(item)["values"][0]
            menu = Menu(self.root, tearoff=0)
            menu.add_command(label=f"删除玩家: {name}", command=lambda: self.delete_player_action(name))
            menu.post(event.x_root, event.y_root)

    def show_team_menu(self, event):
        item = self.team_table.identify_row(event.y)
        if item:
            self.team_table.selection_set(item)
            data = self.team_table.item(item)["values"]
            menu = Menu(self.root, tearoff=0)
            menu.add_command(label="删除此条阵容", command=lambda: self.delete_team_action(data[4]))
            menu.post(event.x_root, event.y_root)

    def delete_player_action(self, name):
        if messagebox.askyesno("确认", f"确定删除玩家【{name}】及其所有记录吗？"):
            self.db.delete_player(name)
            self.refresh_player_list()
            self.team_table.delete(*self.team_table.get_children())

    def delete_team_action(self, thash):
        if messagebox.askyesno("确认", "确定删除这条阵容记录吗？"):
            self.db.delete_team(thash)
            self.on_player_select(None)

    def open_trust_mgr(self):
        TrustManager(self.root, self.db, None)

    def on_player_select(self, e):
        sel = self.player_list.selection()
        if not sel: return
        p_name = self.player_list.item(sel[0])["values"][0]
        self.team_table.delete(*self.team_table.get_children())
        with sqlite3.connect(self.db.db_name) as conn:
            data = conn.execute("SELECT team_json, first_seen, team_hash FROM teams WHERE player_name = ? ORDER BY first_seen DESC", (p_name,)).fetchall()
            for tj, tt, th in data:
                t = json.loads(tj)
                self.team_table.insert("", END, values=(tt, t[0], t[1], t[2], th))

    def select_area(self):
        win = Toplevel(); win.attributes('-fullscreen', True, '-alpha', 0.3)
        c = Canvas(win, cursor="cross"); c.pack(fill=BOTH, expand=True)
        res = {"v": None}; self.sx = self.sy = 0; self.rect = None
        def on_down(e): self.sx, self.sy = e.x, e.y
        def on_move(e):
            if self.rect: c.delete(self.rect)
            self.rect = c.create_rectangle(self.sx, self.sy, e.x, e.y, outline="green", width=2)
        def on_up(e):
            res["v"] = (min(self.sx, e.x), min(self.sy, e.y), abs(e.x-self.sx), abs(e.y-self.sy))
            win.destroy()
        c.bind("<Button-1>", on_down); c.bind("<B1-Motion>", on_move); c.bind("<ButtonRelease-1>", on_up)
        self.root.wait_window(win)
        return res["v"]

    def set_icon_reg(self): self.root.iconify(); self.config["icon_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_name_reg(self): self.root.iconify(); self.config["name_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_block_reg(self): self.root.iconify(); self.config["block_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_gen_regs_auto(self):
        self.root.iconify(); r = self.select_area(); self.root.deiconify()
        if r:
            x, y, w, h = r; uw = w // 3
            self.config["gen_regs"] = [(x + 2 * uw, y, uw, h), (x + uw, y, uw, h), (x, y, uw, h)]
            self._save_config()

if __name__ == "__main__":
    tk_root = Tk(); app = App(tk_root); tk_root.mainloop()