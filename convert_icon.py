#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 Logo.jpg 转换为 logo.ico"""

import os

def convert_icon():
    # 优先使用用户指定的图标源，否则使用项目自带的
    base_dir = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(base_dir, 'Logo.jpg')
    target = os.path.join(base_dir, 'logo.ico')

    # 优先使用项目目录的 logo.ico
    local_ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.ico')
    if os.path.exists(local_ico) and os.path.getsize(local_ico) > 0:
        print(f"logo.ico 已存在，跳过转换")
        return True

    if not os.path.exists(source):
        print(f"源图标不存在: {source}")
        return False

    try:
        from PIL import Image
        img = Image.open(source)
        # 转换为 RGBA 模式
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        # 生成多尺寸 ICO（16, 32, 48, 64, 128, 256）
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(target, format='ICO', sizes=sizes)
        print(f"图标转换成功: {target}")
        return True
    except ImportError:
        print("Pillow 未安装，无法转换图标")
        return False
    except Exception as e:
        print(f"图标转换失败: {e}")
        return False


if __name__ == '__main__':
    convert_icon()
