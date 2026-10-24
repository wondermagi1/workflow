#!/usr/bin/env python3
"""CodeFlow 三 Agent 代码工作流的网页启动入口。

用法::

    python workflow.py --open      # 默认 http://127.0.0.1:8010/
    python workflow.py --port 8011 --open

webui.py 是独立的基础单 Agent 网页入口，默认端口为 8000。
"""
import sys
if sys.version_info < (3, 10):
    raise SystemExit("需要 Python 3.10 或更新版本。")

import argparse
import os
import webbrowser
from dataclasses import replace
from pathlib import Path
from agent.config import AgentConfig
from codeflow.server import make_server


def main():
    parser = argparse.ArgumentParser(description="CodeFlow · 三 Agent 代码工作流，默认网页 http://127.0.0.1:8010/")
    parser.add_argument("--port", type=int, default=8010, help="监听端口，默认 8010")
    parser.add_argument("--open", action="store_true", help="启动后自动用浏览器打开工作台")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    config = AgentConfig.from_env(workspace=root, max_retries=1, request_timeout=120, read_only=True, allow_exec=False)
    if not (os.getenv("CODE_AGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL")):
        config = replace(config, base_url="https://api.deepseek.com")
    if not (os.getenv("CODE_AGENT_MODEL") or os.getenv("OPENAI_MODEL")):
        config = replace(config, model="deepseek-flash")
    if config.api_key in ("replace_with_your_deepseek_api_key", "your_deepseek_api_key"):
        config = replace(config, api_key="")
    server = make_server(config, port=args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("CodeFlow 已启动：" + url, flush=True)
    print("模型：" + config.model + (" · 已配置" if config.api_key else " · 未配置密钥，可使用离线演示"), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
