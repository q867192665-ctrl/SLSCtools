@echo off
chcp 65001 >nul
echo ========================================
echo  ChinanTool Build Script
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python
    pause
    exit /b 1
)

REM 检查 PyInstaller
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 PyInstaller...
    pip install pyinstaller
)

REM 检查 Pillow（图标转换需要）
pip show pillow >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 Pillow...
    pip install pillow
)

echo.
echo [1/4] 转换图标...
python convert_icon.py
if errorlevel 1 (
    echo [警告] 图标转换失败，将使用默认图标
)

echo.
echo [2/4] 打包后端程序 (chinantool_backend.exe)...
pyinstaller --noconfirm --onefile --noconsole ^
    --name "chinantool_backend" ^
    --icon "logo.ico" ^
    --hidden-import "uvicorn.logging" ^
    --hidden-import "uvicorn.loops" ^
    --hidden-import "uvicorn.loops.auto" ^
    --hidden-import "uvicorn.protocols" ^
    --hidden-import "uvicorn.protocols.http" ^
    --hidden-import "uvicorn.protocols.http.auto" ^
    --hidden-import "uvicorn.protocols.websockets" ^
    --hidden-import "uvicorn.protocols.websockets.auto" ^
    --hidden-import "uvicorn.lifespan" ^
    --hidden-import "uvicorn.lifespan.on" ^
    --hidden-import "yaml" ^
    --hidden-import "routeros_api" ^
    --hidden-import "routeros_api.api_structure" ^
    --hidden-import "routeros_api.resource" ^
    --hidden-import "routeros_api.socket_api" ^
    --hidden-import "routeros_api.communication" ^
    --hidden-import "routeros_api.api_communication" ^
    --hidden-import "websockets" ^
    --hidden-import "websockets.legacy" ^
    --hidden-import "websockets.legacy.server" ^
    --hidden-import "psutil" ^
    --hidden-import "PIL" ^
    main.py

if errorlevel 1 (
    echo [错误] 后端打包失败
    pause
    exit /b 1
)

echo.
echo [3/4] 打包启动器 (ChinanTool.exe)...
pyinstaller --noconfirm --onefile --noconsole ^
    --name "ChinanTool" ^
    --icon "logo.ico" ^
    --hidden-import "yaml" ^
    launcher.py

if errorlevel 1 (
    echo [错误] 启动器打包失败
    pause
    exit /b 1
)

echo.
echo [4/4] 整理输出文件...
if not exist "dist\setup_files" mkdir "dist\setup_files"
copy /Y "dist\chinantool_backend.exe" "dist\setup_files\" >nul
copy /Y "dist\ChinanTool.exe" "dist\setup_files\" >nul
copy /Y "config.yaml" "dist\setup_files\" >nul
if exist "logo.ico" copy /Y "logo.ico" "dist\setup_files\" >nul
if exist "Logo.jpg" copy /Y "Logo.jpg" "dist\setup_files\" >nul
if exist "SLSCtools.exe" copy /Y "SLSCtools.exe" "dist\setup_files\" >nul
if exist "unlock.py" copy /Y "unlock.py" "dist\setup_files\" >nul

REM 复制 static 目录
if exist "dist\setup_files\static" rd /s /q "dist\setup_files\static"
xcopy /E /I /Y "static" "dist\setup_files\static" >nul

echo.
echo ========================================
echo  打包完成！
echo  输出目录: dist\setup_files\
echo ========================================
echo.
pause
