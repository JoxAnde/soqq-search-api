#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
so.qq.com（腾讯元宝搜索·传统网页检索模式）标准搜索 API

对外提供通用的 HTTP 搜索接口，参数与返回结构对齐常见搜索 API 规范：

  GET /api/search?q=<关键词>&pn=<页码>&rn=<每页条数>

返回（标准包装）：
{
  "code": 0,
  "message": "ok",
  "data": {
    "query": "steam",
    "page": 1,
    "page_size": 10,
    "total": 10,
    "results": [
      {
        "title": "结果标题",
        "link":   "https://...",
        "site":   "来源名称",
        "host":   "https://www.example.com",
        "snippet": "摘要",
        "date":   "2026-10-24",
        "favicon": "https://..."
      }
    ]
  }
}

错误码：
  0   成功
  400 参数错误（缺关键词 / 分页参数非法）
  502 上游抓取或解析失败

兼容旧版：
  - 路由 /search、/api/search 均可访问
  - 参数 query→q、page→pn、size→rn 兼容读取

实现方式：
  目标站点 /isearch 为 Next.js SSR 页面，结果以转义 JSON 内嵌在 RSC 数据流
  （self.__next_f.push）中；本服务拉取 HTML 后在本地解析标准结果卡字段，
  不依赖浏览器、不保存任何登录态。

合规与限制（务必阅读）：
  - 仅作个人学习/自用的检索代理，请勿高频、批量抓取，以免对目标站点造成压力。
  - 返回内容版权归原网站与作者所有；对外提供服务前需遵守 so.qq.com 用户协议、
    隐私政策及相关法律法规，并自行承担合规责任。
  - 页面结构（字段名、RSC 格式）由站点前端决定，若上游改版需相应调整解析正则。
  - 本服务不携带 Cookie / 登录态；个性化、历史记录等能力不在代理范围内。

运行：
  python3 soqq_search_api.py            # 默认 127.0.0.1:8787
  PORT=9000 python3 soqq_search_api.py
"""

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
BASE_URL = "https://so.qq.com/isearch"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15
MAX_RN = 30             # 单页最多返回条数
DEFAULT_RN = 10
RATE_WINDOW = 1.0      # 秒：同一来源两次上游请求最小间隔（简单节流）
_last_fetch = [0.0]

# 标准结果卡字段（在单个卡片块内抽取，避免跨对象误匹配）
FIELD_PAT = {
    "title": r'"title":"(.*?)"',
    "url": r'"url":"(.*?)"',
    "host": r'"host":"(.*?)"',
    "content": r'"content":"(.*?)"',
    "source": r'"sourcename":"(.*?)"',
    "date": r'"date":"(.*?)"',
    "favicon": r'"favicon":"(.*?)"',
}
CARD_NAME = "card-pc/card_normal_result"
RSC_PAT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _unescape_rsc(joined: str) -> str:
    """把 RSC 字符串中的常见转义还原为可读文本。"""
    joined = joined.replace('\\"', '"').replace('\\/', '/')
    joined = joined.replace('\\n', ' ').replace('\\t', ' ')
    joined = joined.replace('\\u0026', '&').replace('\\u003c', '<').replace('\\u003e', '>')
    return joined


def _clean(text: str) -> str:
    text = html.unescape(text)
    # 去除上游用于高亮命中词的私有区标记（如 U+E40A / U+E40B 等）
    text = re.sub(r'[\ue000-\uf8ff]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_html(query: str, page: int) -> str:
    """拉取上游检索结果页 HTML（带简单节流与重试）。"""
    params = {
        "query": query,
        "quick": "1",
        "page": str(page),
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://so.qq.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    # 简单节流：避免连续瞬时打满上游
    gap = RATE_WINDOW - (time.time() - _last_fetch[0])
    if gap > 0:
        time.sleep(gap)

    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                _last_fetch[0] = time.time()
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"上游请求失败: {last_err}")


def _field(block: str, key: str) -> str:
    m = re.search(FIELD_PAT[key], block)
    return _clean(m.group(1)) if m else ""


def parse_results(html_text: str) -> list[dict]:
    """从 SSR HTML 的 RSC 数据流中解析标准网页结果卡。

    先按 cardName 把每个标准结果卡切成独立块，再在块内提取字段，
    避免翻译卡、聚合卡等嵌套 JSON 造成跨对象误匹配。
    """
    chunks = RSC_PAT.findall(html_text)
    joined = _unescape_rsc("".join(chunks))

    # 按卡片名切分：每个块从一个 card_normal_result 开始，到下一个卡片名为止
    blocks = []
    starts = [m.start() for m in re.finditer(re.escape(CARD_NAME), joined)]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(joined)
        blocks.append(joined[start:end])

    results = []
    seen = set()
    for block in blocks:
        link = _field(block, "url")
        if not link or link in seen:
            continue
        seen.add(link)
        results.append({
            "title": _field(block, "title"),
            "link": link,
            "site": _field(block, "source"),
            "host": _field(block, "host"),
            "snippet": _field(block, "content"),
            "date": _field(block, "date"),
            "favicon": _field(block, "favicon"),
        })
    return results


class Handler(BaseHTTPRequestHandler):
    server_version = "soqq-search-proxy/1.0"

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _pick(qs: dict, keys: list[str], default: str) -> str:
        for k in keys:
            if k in qs and qs[k] and qs[k][0].strip():
                return qs[k][0].strip()
        return default

    def _ok(self, data: dict):
        self._send_json({"code": 0, "message": "ok", "data": data})

    def _fail(self, code: int, message: str, http_status: int):
        self._send_json({"code": code, "message": message, "data": None}, http_status)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/health":
            return self._send_json({"code": 0, "message": "ok",
                                    "data": {"service": "soqq-search-proxy"}})

        # 兼容新旧路由
        if parsed.path not in ("/api/search", "/search"):
            return self._fail(404, "not_found", 404)

        qs = urllib.parse.parse_qs(parsed.query)
        # 兼容参数：q/query、pn/page、rn/size
        query = self._pick(qs, ["q", "query"], "")
        page_raw = self._pick(qs, ["pn", "page"], "1")
        rn_raw = self._pick(qs, ["rn", "size"], str(DEFAULT_RN))

        if not query:
            return self._fail(400, "参数 q 不能为空（搜索关键词）", 400)

        try:
            page = max(1, int(page_raw))
            rn = min(MAX_RN, max(1, int(rn_raw)))
        except ValueError:
            return self._fail(400, "pn/rn 需为整数", 400)

        try:
            html_text = fetch_html(query, page)
            results = parse_results(html_text)[:rn]
            self._ok({
                "query": query,
                "page": page,
                "page_size": rn,
                "total": len(results),
                "results": results,
            })
        except Exception as e:  # noqa: BLE001
            self._fail(502, f"上游抓取或解析失败: {e}", 502)

    def log_message(self, fmt, *args):  # 静默访问日志，按需打开
        pass


def main():
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[soqq-search-proxy] 监听 http://{host}:{port}")
    print(f"示例: http://{host}:{port}/api/search?q=steam&pn=1&rn=10")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
