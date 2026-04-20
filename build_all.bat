@echo off
chcp 65001 >nul
cd /d "e:\程序\chinantool"

echo ========================================
echo  ChinanTool Build Script (No Console)
echo ========================================

REM 清理
if exist build rd /s /q build
if exist dist rd /s /q dist

echo.
echo [1/3] 打包后端...
pyinstaller --noconfirm --onefile --noconsole --name "chinantool_backend" --icon "logo.ico" --hidden-import "uvicorn.logging" --hidden-import "uvicorn.loops" --hidden-import "uvicorn.loops.auto" --hidden-import "uvicorn.protocols" --hidden-import "uvicorn.protocols.http" --hidden-import "uvicorn.protocols.http.auto" --hidden-import "uvicorn.protocols.websockets" --hidden-import "uvicorn.protocols.websockets.auto" --hidden-import "uvicorn.lifespan" --hidden-import "uvicorn.lifespan.on" --hidden-import "yaml" --hidden-import "routeros_api" --hidden-import "routeros_api.api_structure" --hidden-import "routeros_api.resource" --hidden-import "routeros_api.socket_api" --hidden-import "routeros_api.communication" --hidden-import "routeros_api.api_communication" --hidden-import "websockets" --hidden-import "websockets.legacy" --hidden-import "websockets.legacy.server" --hidden-import "psutil" --hidden-import "PIL" main.py
if errorlevel 1 (
    echo [ERROR] 后端打包失败
    pause
    exit /b 1
)

echo.
echo [2/3] 打包启动器...
pyinstaller --noconfirm --onefile --noconsole --name "ChinanTool" --icon "logo.ico" --hidden-import "yaml" launcher.py
if errorlevel 1 (
    echo [ERROR] 启动器打包失败
    pause
    exit /b 1
)

echo.
echo [3/3] 整理文件 + 编译安装包...
if not exist "dist\setup_files" mkdir "dist\setup_files"
copy /Y "dist\chinantool_backend.exe" "dist\setup_files\"
copy /Y "dist\ChinanTool.exe" "dist\setup_files\"
copy /Y "config.yaml" "dist\setup_files\"
if exist "logo.ico" copy /Y "logo.ico" "dist\setup_files\"
if exist "Logo.jpg" copy /Y "Logo.jpg" "dist\setup_files\"
if exist "dist\setup_files\static" rd /s /q "dist\setup_files\static"
xcopy /E /I /Y "static" "dist\setup_files\static"

"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "e:\程序\chinantool\setup.iss"

echo.
echo ========================================
echo  完成！安装包在 installer_output\
echo ========================================
pause
