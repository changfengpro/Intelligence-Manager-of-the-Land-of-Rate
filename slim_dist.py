import os

# --- 配置：确保这里的名称和你打包出来的文件夹名一致 ---
APP_NAME = "率土情报管家"

def slim_down():
    # 定位到 torch 的库目录
    # 注意：PyInstaller 6.x 以后版本通常放在 _internal 文件夹下
    torch_lib = os.path.join("dist", APP_NAME, "_internal", "torch", "lib")
    
    if not os.path.exists(torch_lib):
        print(f"❌ 错误: 未发现路径 {torch_lib}")
        print("请检查你的打包模式是否为 --onedir 以及 APP_NAME 是否正确。")
        return

    # 占用空间巨大但在 CPU 模式下完全用不到的关键词
    useless_keywords = [
        "nvrtc", "cudnn", "cublas", "cufft", "curand", 
        "cusolver", "cusparse", "nvjitlink", "nvfatbin"
    ]
    
    print(f"🔍 正在清理: {torch_lib} ...")
    
    count = 0
    size_saved = 0
    
    # 遍历并删除
    for file in os.listdir(torch_lib):
        if any(key in file.lower() for key in useless_keywords) and file.endswith(".dll"):
            file_path = os.path.join(torch_lib, file)
            try:
                f_size = os.path.getsize(file_path)
                os.remove(file_path)
                size_saved += f_size
                count += 1
                print(f"已移除: {file} ({(f_size/1024/1024):.1f} MB)")
            except Exception as e:
                print(f"跳过 {file}: {e}")

    print("\n" + "="*40)
    print(f"✅ 瘦身完成！")
    print(f"移除文件总数: {count} 个")
    print(f"腾出空间: {(size_saved/1024/1024):.1f} MB")
    print("="*40)
    print(f"现在可以尝试运行: dist\\{APP_NAME}\\{APP_NAME}.exe")

if __name__ == "__main__":
    slim_down()