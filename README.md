# so.qq.com 标准搜索 API（腾讯元宝搜索 · 传统网页检索模式）

把 `https://so.qq.com/isearch?query=<关键词>` 的网页检索结果封装成通用标准 HTTP 搜索接口。
纯 Python 标准库实现，无需安装第三方依赖。

## 运行

```bash
python3 soqq_search_api.py            # 默认 127.0.0.1:8787
# 或自定义端口/地址
PORT=9000 HOST=0.0.0.0 python3 soqq_search_api.py
```

## 接口

### `GET /api/search`（标准）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `q` | string | 必填 | 搜索关键词（URL 编码） |
| `pn` | int | 1 | 页码，从 1 开始 |
| `rn` | int | 10 | 单页返回条数，上限 30 |

兼容旧版：路由 `/search`，参数 `query` / `page` / `size` 仍可使用。

### `GET /health`

健康检查。

## 标准返回结构

```json
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
        "title": "欢迎来到 Steam",
        "link": "https://s.team/",
        "site": "Steam",
        "host": "https://s.team",
        "snippet": "页面摘要……",
        "date": "2026-10-24",
        "favicon": "https://cdn-yb.icon.qq.com/zuowei_dir/s_team.ico"
      }
    ]
  }
}
```

### 结果字段

| 字段 | 含义 |
|------|------|
| `title` | 结果标题（已去除高亮标记） |
| `link` | 目标链接 |
| `site` | 来源名称 |
| `host` | 来源域名 |
| `snippet` | 摘要 |
| `date` | 发布日期（部分为空） |
| `favicon` | 站点图标 |

### 错误码

| code | HTTP | 含义 |
|------|------|------|
| 0 | 200 | 成功 |
| 400 | 400 | 参数错误（缺关键词 / 分页参数非法） |
| 404 | 404 | 路由不存在 |
| 502 | 502 | 上游抓取或解析失败 |

## 示例

```bash
curl "http://127.0.0.1:8787/api/search?q=steam&pn=1&rn=10"
curl "http://127.0.0.1:8787/api/search?q=%E6%83%A0%E5%B7%9E%E5%A4%A9%E6%B0%94"
```

## 实现原理

1. 请求上游 `https://so.qq.com/isearch?query=...&quick=1&page=...`，携带浏览器 UA 与 Referer。
2. 页面是 Next.js SSR，结果以转义 JSON 嵌在 RSC 数据流（`self.__next_f.push`）中。
3. 按标准结果卡 `card-pc/card_normal_result` 切块，在块内抽取
   `title / url / host / content / sourcename / date / favicon`，并去除高亮私有区标记。

## 已知限制与合规提醒

- **结构依赖**：字段名与 RSC 格式由上游前端决定，站点改版后需同步调整解析正则。
- **无登录态**：不携带 Cookie，无法获取个性化、历史记录等需登录能力。
- **风控**：上游有反爬/验证码（`newanti/uigs`、`approve`）。请仅低频、个人学习自用；高频抓取可能被限流或封 IP。
- **版权与合规**：返回内容版权归原网站与作者所有；对外提供服务前须遵守 so.qq.com 用户协议、隐私政策及中国大陆相关法律法规，由此产生的责任由使用方自行承担。
- **翻页**：当前通过 `page` 参数翻页；若上游改用其他分页字段，需在 `fetch_html` 中调整。
