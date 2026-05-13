#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试用：与 login.py 相同（扫码、写 ~/.agent-browser/auth/boss-recruit.json、持久化 recruit_profile），
但登录成功后不立即关浏览器，按 Enter 后再关，便于确认已登录态或再点进沟通页等。
"""

from __future__ import annotations

import os
import sys

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from login import login  # noqa: E402


if __name__ == "__main__":
    ok = login(keep_open=True)
    sys.exit(0 if ok else 1)
