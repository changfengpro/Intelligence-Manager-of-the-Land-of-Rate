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
import csv
from datetime import datetime
from tkinter import *
from tkinter import ttk, messagebox, filedialog
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
            
            try:
                c.execute("SELECT note FROM teams LIMIT 1")
            except sqlite3.OperationalError:
                c.execute("ALTER TABLE teams ADD COLUMN note TEXT DEFAULT ''")
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
    
    def update_team(self, team_hash, new_team_list, new_note):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("UPDATE teams SET team_json = ?, note = ? WHERE team_hash = ?", 
                         (json.dumps(new_team_list, ensure_ascii=False), new_note, team_hash))
            conn.commit()

    def save_record(self, player_name, team_list):
        while len(team_list) < 3: team_list.append("未知 · 未知")
        names_only = [t.split(" · ")[1] if " · " in t else t for t in team_list]
        team_hash = hashlib.md5(f"{player_name}{''.join(names_only)}".encode('utf-8')).hexdigest()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO players VALUES (?, ?)", (player_name, now))
            c.execute("INSERT OR IGNORE INTO teams (player_name, team_json, team_hash, first_seen, note) VALUES (?, ?, ?, ?, ?)", 
                      (player_name, json.dumps(team_list, ensure_ascii=False), team_hash, now, ""))
            conn.commit()

    def export_to_csv(self, filename):
        with sqlite3.connect(self.db_name) as conn:
            sql = """
            SELECT t.player_name, t.first_seen, t.team_json, t.note, tr.name 
            FROM teams t 
            LEFT JOIN trust_list tr ON t.player_name = tr.name
            ORDER BY t.first_seen DESC
            """
            rows = conn.execute(sql).fetchall()
        try:
            with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['玩家名称', '是否白名单', '记录时间', '大营', '中军', '前锋', '备注'])
                for row in rows:
                    p_name, f_seen, t_json, note, trusted_name = row
                    is_trusted = "是" if trusted_name else "否"
                    try:
                        gens = json.loads(t_json)
                        while len(gens) < 3: gens.append("未知")
                    except:
                        gens = ["未知", "未知", "未知"]
                    writer.writerow([p_name, is_trusted, f_seen, gens[0], gens[1], gens[2], note])
            return True, f"成功导出 {len(rows)} 条记录"
        except Exception as e:
            return False, str(e)

    def import_from_csv(self, filename):
        count = 0
        try:
            with open(filename, 'r', encoding='utf-8-sig') as csvfile:
                reader = csv.reader(csvfile)
                header = next(reader, None)
                if not header: return False, "空文件"
                with sqlite3.connect(self.db_name) as conn:
                    c = conn.cursor()
                    for row in reader:
                        if len(row) < 7: continue
                        p_name = row[0].strip()
                        is_trusted = row[1].strip()
                        f_seen = row[2].strip()
                        gens = [row[3].strip(), row[4].strip(), row[5].strip()]
                        note = row[6].strip()
                        if not p_name: continue
                        if is_trusted == "是":
                            c.execute("INSERT OR IGNORE INTO trust_list VALUES (?)", (p_name,))
                        c.execute("INSERT OR REPLACE INTO players VALUES (?, ?)", (p_name, f_seen))
                        names_only = [t.split(" · ")[1] if " · " in t else t for t in gens]
                        team_hash = hashlib.md5(f"{p_name}{''.join(names_only)}".encode('utf-8')).hexdigest()
                        team_json = json.dumps(gens, ensure_ascii=False)
                        c.execute("""
                            INSERT INTO teams (player_name, team_json, team_hash, first_seen, note) 
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(team_hash) DO UPDATE SET note = excluded.note
                        """, (p_name, team_json, team_hash, f_seen, note))
                        count += 1
                    conn.commit()
            return True, f"成功导入 {count} 条记录"
        except Exception as e:
            return False, f"导入失败: {e}"

# ==================== 3. 弹窗 UI ====================

class AboutDialog(Toplevel):
    """【新增】关于页面，你可以在此处自由编辑文字内容"""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("关于率土情报管家")
        self.geometry("480x500")
        self.configure(bg="#ffffff")
        self.resizable(False, False)

        # --- 这里可以自由编辑内容 ---
        self.info_text = """
                        ━━━━━━━━━━━━━━━━━━━━━━━━━━
                        🏰 率土情报管家 v1.2 
                        ━━━━━━━━━━━━━━━━━━━━━━━━━━

            【核心功能】
            1. 实时监控：自动识别战报中的敌方阵容及玩家信息。
            2. 智能校对：内置武将库与相似度算法，自动修正OCR错词。
            3. 数据沉淀：支持备注记录、多战报合并、白名单管理。
            4. 自由交互：支持 Excel 格式导入导出，方便多端同步。

            【使用须知】
            ● 建议在游戏窗口化模式下使用，效果最佳。
            ● 识别武将时，请先拉取一个能覆盖三名武将的长方形区域。
            ● 若遇到生僻字无法识别，可在‘武将列表.txt’中手动添加。

            【开发者信息】
            开发者：天丨暗星
            版本更新日期：2025年12月
            联系/反馈：[info@rmer-hy.ip-ddns.com]

            [ 本程序仅供技术交流学习使用，严禁用于任何非法目的 ]
"""
        
        # UI 布局
        header = Label(self, text="ℹ️ 软件关于与帮助", font=("微软雅黑", 14, "bold"), bg="#2c3e50", fg="white", pady=15)
        header.pack(fill=X)

        text_area = Text(self, font=("微软雅黑", 10), bg="#ffffff", relief=FLAT, padx=25, pady=20, spacing1=5)
        text_area.insert(END, self.info_text)
        text_area.config(state=DISABLED) # 设置为只读
        text_area.pack(fill=BOTH, expand=True)

        footer_btn = ttk.Button(self, text="我知道了", command=self.destroy)
        footer_btn.pack(pady=15)
        
        # 模态窗口设置
        self.transient(parent)
        self.grab_set()

class SimilarityDialog(Toplevel):
    def __init__(self, parent, new_name, old_name):
        super().__init__(parent)
        self.title("重名检查")
        self.geometry("450x250")
        self.configure(bg="#fdfcfb")
        self.result = None
        self.trust_var = BooleanVar(value=False)
        
        Label(self, text="⚠️ 发现高度相似玩家名", font=("微软雅黑", 12, "bold"), fg="#e67e22", bg="#fdfcfb").pack(pady=15)
        Label(self, text=f"识别结果: {new_name}", font=("微软雅黑", 10), bg="#fdfcfb").pack()
        Label(self, text=f"数据库已有: {old_name}", font=("微软雅黑", 10, "bold"), fg="#2980b9", bg="#fdfcfb").pack(pady=5)
        
        btn_f = Frame(self, bg="#fdfcfb")
        btn_f.pack(pady=20)
        ttk.Button(btn_f, text=f"使用新名", command=lambda: self.done("use_new"), width=15).pack(side=LEFT, padx=10)
        ttk.Button(btn_f, text=f"保留旧名", command=lambda: self.done("keep_old"), width=15).pack(side=LEFT, padx=10)
        
        Checkbutton(self, text="记住选择，不再询问", variable=self.trust_var, bg="#fdfcfb", font=("微软雅黑", 9)).pack()
        self.grab_set()

    def done(self, action):
        self.result = (action, self.trust_var.get())
        self.destroy()

class TrustManager(Toplevel):
    def __init__(self, parent, db, callback):
        super().__init__(parent)
        self.title("白名单管理")
        self.geometry("350x450")
        self.db = db
        self.callback = callback
        
        Label(self, text="🛡 已信任的玩家 ID", font=("微软雅黑", 11, "bold"), pady=10).pack()
        self.listbox = Listbox(self, font=("微软雅黑", 10), selectbackground="#3498db", borderwidth=0, highlightthickness=1)
        self.listbox.pack(fill=BOTH, expand=True, padx=20, pady=5)
        
        self.refresh()
        ttk.Button(self, text="移除选中记录", command=self.remove_name).pack(pady=15)

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

class EditTeamDialog(Toplevel):
    def __init__(self, parent, db, team_hash, current_data, callback):
        super().__init__(parent)
        self.title("编辑阵容与备注")
        self.geometry("500x350")
        self.db, self.team_hash, self.callback = db, team_hash, callback
        self.configure(bg="#ecf0f1")
        self.gens = [current_data[1], current_data[2], current_data[3]]
        self.note_txt = current_data[5]

        Label(self, text="✏️ 修改阵容信息", font=("微软雅黑", 12, "bold"), bg="#ecf0f1").pack(pady=15)
        self.entries = []
        labels = ["大营", "中军", "前锋"]
        input_frame = Frame(self, bg="#ecf0f1")
        input_frame.pack(pady=5)

        for i in range(3):
            f = Frame(input_frame, bg="#ecf0f1")
            f.pack(fill=X, pady=5)
            Label(f, text=f"{labels[i]}:", width=6, bg="#ecf0f1", anchor=E).pack(side=LEFT)
            e = ttk.Entry(f, width=30)
            e.insert(0, self.gens[i])
            e.pack(side=LEFT, padx=10)
            self.entries.append(e)

        Label(self, text="📝 备注 / 战法提示", font=("微软雅黑", 10, "bold"), bg="#ecf0f1").pack(pady=(20, 5))
        self.note_entry = ttk.Entry(self, width=45)
        self.note_entry.insert(0, self.note_txt)
        self.note_entry.pack()

        btn_f = Frame(self, bg="#ecf0f1")
        btn_f.pack(pady=25)
        ttk.Button(btn_f, text="保存修改", command=self.save).pack(side=LEFT, padx=10)
        ttk.Button(btn_f, text="取消", command=self.destroy).pack(side=LEFT, padx=10)
        self.transient(parent); self.grab_set()

    def save(self):
        new_gens = [e.get().strip() for e in self.entries]
        new_note = self.note_entry.get().strip()
        self.db.update_team(self.team_hash, new_gens, new_note)
        self.callback()
        self.destroy()

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
        self.root.title("率土情报管家 v1.2 (专业CSV版)")
        self.root.geometry("1280x800")
        self.root.configure(bg="#f5f6f7")
        
        self.db = DatabaseManager()
        self.engine = RecognitionEngine()
        self.config_file = "config.json"
        self.config = {"icon_reg": None, "name_reg": None, "gen_regs": [], "block_reg": None}
        self.is_monitoring = False
        
        self._set_style()
        self._load_saved_config()
        self._build_ui()
        self.refresh_player_list()

    def _set_style(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Treeview", font=("微软雅黑", 10), rowheight=30, background="white", fieldbackground="white")
        style.map("Treeview", background=[('selected', '#3498db')])
        style.configure("Treeview.Heading", font=("微软雅黑", 10, "bold"), background="#ecf0f1", foreground="#2c3e50")
        style.configure("Action.TButton", font=("微软雅黑", 9))

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
        # --- 顶部导航栏 ---
        top_bar = Frame(self.root, bg="#2c3e50", height=70)
        top_bar.pack(fill=X); top_bar.pack_propagate(False)
        
        title_label = Label(top_bar, text="🏰 率土情报管家", font=("微软雅黑", 14, "bold"), fg="white", bg="#2c3e50")
        title_label.pack(side=LEFT, padx=20)

        btn_f = Frame(top_bar, bg="#2c3e50")
        btn_f.pack(side=LEFT, padx=10)
        
        for idx, (txt, cmd) in enumerate([("1. 框战报", self.set_icon_reg), ("2. 框玩家", self.set_name_reg), 
                                        ("3. 框武将", self.set_gen_regs_auto), ("4. 框干扰", self.set_block_reg)]):
            ttk.Button(btn_f, text=txt, command=cmd, style="Action.TButton", width=10).pack(side=LEFT, padx=3)
        
        ttk.Button(btn_f, text="🛡 白名单", command=self.open_trust_mgr, width=8).pack(side=LEFT, padx=10)
        ttk.Button(btn_f, text="⬇ 导入数据", command=self.import_action, width=10).pack(side=LEFT, padx=5)
        ttk.Button(btn_f, text="⬆ 导出数据", command=self.export_action, width=10).pack(side=LEFT, padx=5)
        
        # 新增关于按钮
        ttk.Button(btn_f, text="ℹ 关于", command=self.show_about_dialog, width=8).pack(side=LEFT, padx=5)

        self.btn_run = Button(top_bar, text="▶ 开始监控", bg="#27ae60", fg="white", font=("微软雅黑", 10, "bold"), 
                             command=self.toggle, width=15, relief=FLAT, cursor="hand2")
        self.btn_run.pack(side=RIGHT, padx=30)
        
        self.status_label = Label(top_bar, text="● 系统就绪", font=("微软雅黑", 10), fg="#bdc3c7", bg="#2c3e50")
        self.status_label.pack(side=RIGHT, padx=10)

        # --- 主体内容区 ---
        main_frame = Frame(self.root, bg="#f5f6f7")
        main_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)

        pw = PanedWindow(main_frame, orient=HORIZONTAL, bg="#dcdde1", sashwidth=4)
        pw.pack(fill=BOTH, expand=True)
        
        left_f = Frame(pw, bg="white")
        pw.add(left_f, width=320)
        
        search_f = Frame(left_f, bg="#f5f6f7", padx=10, pady=10)
        search_f.pack(fill=X)
        self.search_var = StringVar()
        self.search_var.trace("w", lambda *args: self.refresh_player_list())
        search_entry = ttk.Entry(search_f, textvariable=self.search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True)
        ttk.Button(search_f, text="清空", width=5, command=lambda: self.search_var.set("")).pack(side=LEFT, padx=5)

        self.player_list = ttk.Treeview(left_f, columns=("name"), show="headings", selectmode="browse")
        self.player_list.heading("name", text="玩家库 (点击查看详情)")
        self.player_list.pack(fill=BOTH, expand=True, padx=5, pady=5)
        self.player_list.bind("<<TreeviewSelect>>", self.on_player_select)
        self.player_list.bind("<Button-3>", self.show_player_menu)
        
        right_f = Frame(pw, bg="white")
        pw.add(right_f)
        
        detail_title = Label(right_f, text="🗃 侦察记录详情 (双击行可编辑阵容/备注)", font=("微软雅黑", 11, "bold"), bg="white", fg="#34495e", pady=10)
        detail_title.pack(anchor=W, padx=15)

        self.team_table = ttk.Treeview(right_f, columns=("time", "b", "m", "f", "note", "hash"), show="headings")
        self.team_table.heading("time", text="记录时间")
        self.team_table.heading("b", text="大营"); self.team_table.heading("m", text="中军"); self.team_table.heading("f", text="前锋")
        self.team_table.heading("note", text="备注")
        
        self.team_table.column("time", width=140, anchor=CENTER)
        self.team_table.column("b", width=110, anchor=CENTER)
        self.team_table.column("m", width=110, anchor=CENTER)
        self.team_table.column("f", width=110, anchor=CENTER)
        self.team_table.column("note", width=150, anchor=W)
        self.team_table.column("hash", width=0, stretch=NO)
        
        self.team_table.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.team_table.bind("<Button-3>", self.show_team_menu)
        self.team_table.bind("<Double-1>", self.on_team_double_click)
        
        footer = Label(self.root, text="操作提示：右键可删除，双击可编辑备注 | 本工具由 Gemini AI 辅助开发", 
                       font=("微软雅黑", 8), fg="#95a5a6", bg="#f5f6f7", pady=5)
        footer.pack()

    def show_about_dialog(self):
        AboutDialog(self.root)

    def refresh_player_list(self):
        search_term = self.search_var.get().strip().lower()
        self.player_list.delete(*self.player_list.get_children())
        all_names = self.db.get_all_player_names()
        for i, n in enumerate(all_names):
            if not search_term or search_term in n.lower():
                tag = 'even' if i % 2 == 0 else 'odd'
                self.player_list.insert("", END, values=(n,), tags=(tag,))
        self.player_list.tag_configure('odd', background='#f9f9f9')

    def toggle(self):
        if not self.config["name_reg"]: return messagebox.showwarning("提醒", "请先完成各项区域框选")
        self.is_monitoring = not self.is_monitoring
        if self.is_monitoring:
            self.btn_run.config(text="⏹ 停止监控", bg="#e74c3c")
            self.status_label.config(text="● 正在监控中", fg="#2ecc71")
            threading.Thread(target=self.monitor_thread, daemon=True).start()
        else:
            self.btn_run.config(text="▶ 开始监控", bg="#27ae60")
            self.status_label.config(text="● 系统就绪", fg="#bdc3c7")

    def monitor_thread(self):
        while self.is_monitoring:
            if self.config["block_reg"] and self.engine.has_any_text(self.config["block_reg"]):
                self.root.after(0, lambda: self.status_label.config(text="● 受到遮挡干扰", fg="#e67e22"))
                time.sleep(1.2); continue

            if self.engine.check_detail_flag(self.config["icon_reg"]):
                p_name = self.engine.recognize(self.config["name_reg"], True)
                if p_name != "未知玩家":
                    teams = [self.engine.recognize(r) for r in self.config["gen_regs"]]
                    final_name = self.handle_name_logic(p_name)
                    self.db.save_record(final_name, teams)
                    self.root.after(0, self.refresh_player_list)
                    self.root.after(0, lambda: self.status_label.config(text=f"● 已录入: {final_name}", fg="#2ecc71"))
            else:
                self.root.after(0, lambda: self.status_label.config(text="● 等待战报页面...", fg="#f1c40f"))
            time.sleep(1.5)

    def handle_name_logic(self, name):
        # 1. 如果新名字直接就在白名单，直接通过
        if self.db.is_trusted(name): 
            return name
            
        all_names = self.db.get_all_player_names()
        for old in all_names:
            ratio = difflib.SequenceMatcher(None, name, old).ratio()
            # 2. 如果发现相似名字
            if 0.75 <= ratio < 1.0:
                # 【关键修复】：如果这个相似的旧名字已经由于之前的“不再询问”被列入白名单
                # 则直接判定为该旧名字，不再弹窗
                if self.db.is_trusted(old):
                    return old
                
                # 否则，弹出对话框询问
                dialog = SimilarityDialog(self.root, name, old)
                self.root.wait_window(dialog)
                if dialog.result:
                    action, trust = dialog.result
                    if action == "use_new":
                        if trust: self.db.add_to_trust(name) # 以后看到这个新名不再问
                        self.db.rename_player(old, name)
                        return name
                    else:
                        if trust: self.db.add_to_trust(old)  # 以后看到类似这个旧名的都不再问
                        return old
                break
        return name

    def show_player_menu(self, event):
        item = self.player_list.identify_row(event.y)
        if item:
            self.player_list.selection_set(item)
            name = self.player_list.item(item)["values"][0]
            menu = Menu(self.root, tearoff=0)
            menu.add_command(label=f"❌ 删除玩家【{name}】", command=lambda: self.delete_player_action(name))
            menu.post(event.x_root, event.y_root)

    def show_team_menu(self, event):
        item = self.team_table.identify_row(event.y)
        if item:
            self.team_table.selection_set(item)
            data = self.team_table.item(item)["values"]
            menu = Menu(self.root, tearoff=0)
            menu.add_command(label="✏️ 编辑阵容/备注", command=lambda: self.open_edit_dialog(data))
            menu.add_separator()
            menu.add_command(label="🗑 删除此条战报记录", command=lambda: self.delete_team_action(data[5]))
            menu.post(event.x_root, event.y_root)

    def on_team_double_click(self, event):
        item = self.team_table.identify_row(event.y)
        if item:
            data = self.team_table.item(item)["values"]
            self.open_edit_dialog(data)

    def open_edit_dialog(self, data):
        formatted_data = [data[0], data[1], data[2], data[3], data[5], data[4]] 
        EditTeamDialog(self.root, self.db, data[5], formatted_data, lambda: self.on_player_select(None))

    def delete_player_action(self, name):
        if messagebox.askyesno("确认", f"确定彻底删除玩家【{name}】吗？"):
            self.db.delete_player(name)
            self.refresh_player_list()
            self.team_table.delete(*self.team_table.get_children())

    def delete_team_action(self, thash):
        if messagebox.askyesno("确认", "确定删除该阵容记录？"):
            self.db.delete_team(thash)
            self.on_player_select(None)

    def open_trust_mgr(self):
        TrustManager(self.root, self.db, None)

    def export_action(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if path:
            success, msg = self.db.export_to_csv(path)
            if success: messagebox.showinfo("成功", msg)
            else: messagebox.showerror("失败", msg)

    def import_action(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if path:
            success, msg = self.db.import_from_csv(path)
            if success:
                self.refresh_player_list()
                self.team_table.delete(*self.team_table.get_children())
                messagebox.showinfo("成功", msg)
            else: messagebox.showerror("失败", msg)

    def on_player_select(self, e):
        sel = self.player_list.selection()
        if not sel: return
        p_name = self.player_list.item(sel[0])["values"][0]
        self.team_table.delete(*self.team_table.get_children())
        with sqlite3.connect(self.db.db_name) as conn:
            data = conn.execute("SELECT team_json, first_seen, team_hash, note FROM teams WHERE player_name = ? ORDER BY first_seen DESC", (p_name,)).fetchall()
            for i, (tj, tt, th, note) in enumerate(data):
                try: t = json.loads(tj)
                except: t = ["未知", "未知", "未知"]
                tag = 'even' if i % 2 == 0 else 'odd'
                self.team_table.insert("", END, values=(tt, t[0], t[1], t[2], note, th), tags=(tag,))
        self.team_table.tag_configure('odd', background='#f9f9f9')

    def select_area(self):
        win = Toplevel(); win.attributes('-fullscreen', True, '-alpha', 0.25)
        c = Canvas(win, cursor="cross", bg="black"); c.pack(fill=BOTH, expand=True)
        res = {"v": None}
        self.sx = self.sy = 0
        self.rect = None
        def on_down(e): self.sx, self.sy = e.x, e.y
        def on_move(e):
            if self.rect: c.delete(self.rect)
            self.rect = c.create_rectangle(self.sx, self.sy, e.x, e.y, outline="#2ecc71", width=3)
        def on_up(e):
            res["v"] = (min(self.sx, e.x), min(self.sy, e.y), abs(e.x-self.sx), abs(e.y-self.sy))
            win.destroy()
        c.bind("<Button-1>", on_down); c.bind("<B1-Motion>", on_move); c.bind("<ButtonRelease-1>", on_up)
        self.root.wait_window(win)
        return res["v"]

    def set_icon_reg(self): 
        self.root.iconify(); self.config["icon_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_name_reg(self): 
        self.root.iconify(); self.config["name_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_block_reg(self): 
        self.root.iconify(); self.config["block_reg"] = self.select_area(); self.root.deiconify(); self._save_config()
    def set_gen_regs_auto(self):
        self.root.iconify(); r = self.select_area(); self.root.deiconify()
        if r:
            x, y, w, h = r; uw = w // 3
            self.config["gen_regs"] = [(x + 2 * uw, y, uw, h), (x + uw, y, uw, h), (x, y, uw, h)]
            self._save_config()

if __name__ == "__main__":
    tk_root = Tk()
    app = App(tk_root)
    tk_root.mainloop()