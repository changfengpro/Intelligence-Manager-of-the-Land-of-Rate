import os
import shutil
import subprocess
import torch

def build():
    # 1. 环境预检
    print("--- 正在进行环境预检 ---")
    cuda_available = torch.cuda.is_available()
    print(f"PyTorch 版本: {torch.__version__}")
    print(f"CUDA 是否可用: {cuda_available}")
    
    if cuda_available:
        print(f"检测到显卡: {torch.cuda.get_device_name(0)}")
    else:
        print("警告: 当前环境不支持 CUDA。打包后的程序在他人电脑上也将默认使用 CPU。")
        print("建议先运行: pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")

    # 2. 清理旧文件夹
    print("\n--- 正在清理旧的构建文件 ---")
    for folder in ['dist', 'build']:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"已删除 {folder}")

    # 3. 构建打包命令
    # 注意：your_script.py 记得换成你主程序的名字
    main_script = "stzb.py" 
    exe_name = "率土情报管家"
    
    cmd = [
        "pyinstaller",
        "--noconsole",          # 不显示黑窗口
        "--onedir",             # 目录模式（对 EasyOCR 最稳妥）
        f"--name={exe_name}",    # 指定生成的 exe 名字
        "--add-data=武将列表.txt;.",  # 将武将列表打包进根目录
        "--collect-all=easyocr",      # 抓取 easyocr 所有依赖
        "--collect-all=torch",        # 抓取 torch 所有依赖（含 CUDA dll）
        "--collect-submodules=cv2",   # 抓取 opencv 子模块
        "--clean",                    # 打包前清理缓存
        main_script
    ]

    print("\n--- 开始执行 PyInstaller 打包 (这可能需要几分钟) ---")
    try:
        subprocess.run(cmd, check=True)
        print("\n✅ 打包成功！")
        print(f"程序位置: dist/{exe_name}/{exe_name}.exe")
        print(f"提示: 请确保将 '武将列表.txt' 手动也放一份到 dist/{exe_name}/ 文件夹下，方便随时修改。")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 打包失败: {e}")

if __name__ == "__main__":
    build()