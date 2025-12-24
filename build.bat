@echo off
chcp 936
echo ==================================================
echo   率土情报管家 - 稳定性打包模式
echo ==================================================

:: 1. 清理
if exist build rd /s /q build
if exist dist rd /s /q dist

:: 2. 打包
:: 使用 --onedir 避开单文件解压时的 DLL 寻址错误
:: 显式包含 opencv 和 easyocr
pyinstaller --noconsole ^
    --onedir ^
    --name "率土情报管家" ^
    --collect-all easyocr ^
    --add-data "武将列表.txt;." ^
    --clean ^
    stzb.py

echo.
echo ==================================================
echo 打包成功完成！
echo 请将 dist\率土情报管家 整个文件夹拷贝使用。
echo ==================================================
pause