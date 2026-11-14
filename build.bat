@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  打包 FileStudio（Python 3.13 + PyInstaller）
echo ==========================================
echo [1/3] 检查依赖...
py -3.13 -c "import PIL, pypdf; print('依赖 OK')" || (
  echo 缺少依赖，正在安装...
  py -3.13 -m pip install -i https://mirrors.cloud.tencent.com/pypi/simple Pillow send2trash pypdf
)
echo [2/3] 生成图标...
py -3.13 make_icon.py
echo [3/3] 打包为单文件 exe（无控制台窗口）...
py -3.13 -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name FileStudio --icon icon.ico ^
  --add-data "web;web" ^
  --collect-submodules pypdf --collect-submodules send2trash ^
  server.py
if exist "dist\FileStudio.exe" (
  echo.
  echo 打包完成：dist\FileStudio.exe
  echo 双击运行后浏览器打开 http://127.0.0.1:1111
) else (
  echo 打包失败，请查看上方错误输出。
)
pause
