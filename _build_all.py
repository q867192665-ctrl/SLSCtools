#!/usr/bin/env python3
"""一键构建脚本"""
import subprocess
import sys
import os
import shutil

os.chdir(r'e:\程序\chinantool')

# 清理
for d in ['build', 'dist']:
    if os.path.exists(d):
        shutil.rmtree(d, True)
print('[1/5] 已清理旧构建')

# 打包后端
print('[2/5] 打包后端...')
r = subprocess.run([
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm', '--onefile', '--noconsole',
    '--name', 'chinantool_backend',
    '--icon', 'logo.ico',
    '--hidden-import', 'uvicorn.logging',
    '--hidden-import', 'uvicorn.loops',
    '--hidden-import', 'uvicorn.loops.auto',
    '--hidden-import', 'uvicorn.protocols',
    '--hidden-import', 'uvicorn.protocols.http',
    '--hidden-import', 'uvicorn.protocols.http.auto',
    '--hidden-import', 'uvicorn.protocols.websockets',
    '--hidden-import', 'uvicorn.protocols.websockets.auto',
    '--hidden-import', 'uvicorn.lifespan',
    '--hidden-import', 'uvicorn.lifespan.on',
    '--hidden-import', 'yaml',
    '--hidden-import', 'routeros_api',
    '--hidden-import', 'routeros_api.api_structure',
    '--hidden-import', 'routeros_api.resource',
    '--hidden-import', 'routeros_api.socket_api',
    '--hidden-import', 'routeros_api.communication',
    '--hidden-import', 'routeros_api.api_communication',
    '--hidden-import', 'websockets',
    '--hidden-import', 'websockets.legacy',
    '--hidden-import', 'websockets.legacy.server',
    '--hidden-import', 'psutil',
    '--hidden-import', 'PIL',
    'main.py'
], capture_output=False)
if r.returncode != 0:
    print('[错误] 后端打包失败')
    sys.exit(1)
print('[2/5] 后端打包完成')

# 打包启动器
print('[3/5] 打包启动器...')
r = subprocess.run([
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm', '--onefile', '--noconsole',
    '--name', 'ChinanTool',
    '--icon', 'logo.ico',
    '--hidden-import', 'yaml',
    'launcher.py'
], capture_output=False)
if r.returncode != 0:
    print('[错误] 启动器打包失败')
    sys.exit(1)
print('[3/5] 启动器打包完成')

# 整理文件
print('[4/5] 整理文件...')
setup_dir = os.path.join('dist', 'setup_files')
os.makedirs(setup_dir, exist_ok=True)
for f in ['dist/chinantool_backend.exe', 'dist/ChinanTool.exe', 'config.yaml', 'logo.ico', 'Logo.jpg']:
    src = f
    if os.path.exists(src):
        shutil.copy2(src, setup_dir)
        print(f'  复制: {f}')
static_dst = os.path.join(setup_dir, 'static')
if os.path.exists(static_dst):
    shutil.rmtree(static_dst)
shutil.copytree('static', static_dst)
print('  复制: static/')

# Inno Setup
print('[5/5] 编译安装包...')
iscc = r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
r = subprocess.run([iscc, r'e:\程序\chinantool\setup.iss'], capture_output=True)
if r.returncode != 0:
    print('[错误] Inno Setup 编译失败')
    print(r.stdout.decode('gbk', errors='replace')[-2000:])
    print(r.stderr.decode('gbk', errors='replace'))
    sys.exit(1)

print()
print('=' * 40)
print('全部完成！')
print(f'安装包: installer_output/')
print('=' * 40)
