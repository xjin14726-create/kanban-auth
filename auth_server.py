#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西南口腔媒体运营部数据看板 - 企业微信 OAuth2.0 鉴权服务

零依赖实现（仅 Python3 标准库），无需 pip install。
支持企业微信自建应用 snsapi_base 静默授权，校验 UserId 白名单后签发会话。

环境变量（由运维注入，不在代码中硬编码任何凭据）：
  QYWX_CORP_ID       企业ID（ww 开头），必填
  QYWX_AGENT_SECRET  自建应用 Secret，必填
  QYWX_AGENT_ID      自建应用 AgentId，必填
  KANBAN_ALLOWLIST   允许访问的 UserId，逗号分隔（可选，优先级高于文件）
  KANBAN_SESSION_KEY 会话签名密钥（可选，缺省自动生成并持久化）
  KANBAN_PORT        监听端口，默认 8899
  KANBAN_HTML        看板 HTML 路径，默认同目录 index.html

用法：
  python3 auth_server.py
"""
import os
import io
import sys
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import urllib.request
import http.server
import socketserver
import threading
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.environ.get("KANBAN_HTML", os.path.join(BASE_DIR, "index.html"))
ALLOW_FILE = os.path.join(BASE_DIR, "allowlist.txt")
KEY_FILE = os.path.join(BASE_DIR, ".session_key")
TOKEN_FILE = os.path.join(BASE_DIR, ".access_token")
LOG_FILE = os.path.join(BASE_DIR, "logs", "auth.log")
PORT = int(os.environ.get("KANBAN_PORT", "8899"))
SESSION_COOKIE = "kanban_sid"
SESSION_TTL = 12 * 3600          # 会话有效期 12 小时
TOKEN_REFRESH_MARGIN = 300       # access_token 提前 5 分钟刷新

QYWX_API = "https://qyapi.weixin.qq.com"


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        log("FATAL: 缺少环境变量 %s" % name)
    return v


# ---------------- 会话签名密钥 ----------------
def get_session_key():
    k = os.environ.get("KANBAN_SESSION_KEY", "").strip()
    if k:
        return k.encode()
    if os.path.exists(KEY_FILE):
        try:
            return io.open(KEY_FILE, "rb").read().strip()
        except Exception:
            pass
    k = base64.b64encode(os.urandom(32))
    try:
        with io.open(KEY_FILE, "wb") as f:
            f.write(k)
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass
    return k


def sign(payload, key):
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_session(userid, key):
    exp = int(time.time()) + SESSION_TTL
    payload = "%s|%d" % (userid, exp)
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + sign(payload, key)


def check_session(val, key):
    if not val or "." not in val:
        return None
    try:
        b64, sig = val.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(b64.encode()).decode()
        if not hmac.compare_digest(sign(payload, key), sig):
            return None
        uid, exp = payload.rsplit("|", 1)
        if int(exp) < time.time():
            return None
        return uid
    except Exception:
        return None


# ---------------- access_token 缓存 ----------------
_token_lock = threading.Lock()
_token_cache = {"token": None, "expire": 0}


def get_access_token(corpid, secret):
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and _token_cache["expire"] - TOKEN_REFRESH_MARGIN > now:
            return _token_cache["token"]
        # 尝试从文件恢复
        if not _token_cache["token"] and os.path.exists(TOKEN_FILE):
            try:
                d = json.loads(io.open(TOKEN_FILE, encoding="utf-8").read())
                if d.get("expire", 0) - TOKEN_REFRESH_MARGIN > now:
                    _token_cache["token"] = d["token"]
                    _token_cache["expire"] = d["expire"]
                    return d["token"]
            except Exception:
                pass
        url = "%s/cgi-bin/gettoken?corpid=%s&corpsecret=%s" % (
            QYWX_API, urllib.parse.quote(corpid), urllib.parse.quote(secret))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kanban-auth/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode())
            if d.get("errcode") != 0:
                log("gettoken 失败: %s" % d)
                return None
            _token_cache["token"] = d["access_token"]
            _token_cache["expire"] = now + int(d.get("expires_in", 7200))
            try:
                with io.open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(json.dumps(_token_cache))
                os.chmod(TOKEN_FILE, 0o600)
            except Exception:
                pass
            log("access_token 已刷新，有效期 %ss" % d.get("expires_in"))
            return _token_cache["token"]
        except Exception as e:
            log("gettoken 异常: %s" % e)
            return None


def get_userid_by_code(token, code):
    url = "%s/cgi-bin/user/getuserinfo?access_token=%s&code=%s" % (
        QYWX_API, urllib.parse.quote(token), urllib.parse.quote(code))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kanban-auth/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        if d.get("errcode") != 0:
            log("getuserinfo 失败: %s" % d)
            return None, d.get("errmsg", "未知错误")
        # snsapi_base 只返回 UserId；snsapi_userinfo 会返回 user_ticket 等
        return d.get("UserId") or d.get("userid"), None
    except Exception as e:
        log("getuserinfo 异常: %s" % e)
        return None, str(e)


# ---------------- 白名单 ----------------
def load_allowlist():
    raw = os.environ.get("KANBAN_ALLOWLIST", "").strip()
    if raw:
        return set(x.strip() for x in raw.replace("，", ",").split(",") if x.strip())
    s = set()
    if os.path.exists(ALLOW_FILE):
        try:
            for line in io.open(ALLOW_FILE, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    s.add(line)
        except Exception:
            pass
    return s


# ---------------- 页面模板 ----------------
def page(title, body, code=200):
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6eaf2;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
.box{background:#171d29;border:1px solid #242c3b;border-radius:12px;padding:34px 38px;max-width:460px;text-align:center}
h1{font-size:17px;font-weight:600;margin-bottom:12px}
p{font-size:13px;color:#9aa5b8;line-height:1.75}
.badge{display:inline-block;padding:3px 10px;border-radius:10px;font-size:11px;margin-bottom:14px}
.b403{background:rgba(229,72,77,.15);color:#e5484d}
.b500{background:rgba(245,166,35,.15);color:#f5a623}
code{background:#11161f;padding:2px 6px;border-radius:4px;font-size:12px;color:#ffd700}
</style></head><body><div class="box">%s</div></body></html>""" % (title, body)
    return html.encode("utf-8")


def page_403(userid=""):
    body = ('<span class="badge b403">403 无权限</span>'
            "<h1>您没有访问该看板的权限</h1>"
            "<p>当前企业微信身份未被授权访问内部数据看板。<br>"
            "如需开通，请联系运营部管理员将您加入授权名单。</p>"
            + ('<p style="margin-top:14px;font-size:11px;color:#6b7688">身份标识：%s</p>' % userid if userid else ""))
    return page("无权限", body, 403)


def page_500(msg):
    body = ('<span class="badge b500">服务配置异常</span>'
            "<h1>看板鉴权服务未就绪</h1>"
            "<p>%s</p>"
            "<p style=\"margin-top:12px\">请管理员检查服务器环境变量：<br>"
            "<code>QYWX_CORP_ID</code> <code>QYWX_AGENT_SECRET</code> <code>QYWX_AGENT_ID</code></p>" % msg)
    return page("服务异常", body, 500)


def page_wechat_only():
    body = ('<span class="badge b403">需要企业微信</span>'
            "<h1>请在企业微信客户端打开</h1>"
            "<p>内部数据看板仅允许通过企业微信授权访问。<br>"
            "请在企业微信中点击「工作台」里的看板应用进入。</p>")
    return page("需要企业微信", body, 403)


# ---------------- HTTP 处理 ----------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "kanban-auth/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.client_address[0], fmt % args))

    # ---------- 工具 ----------
    def _send(self, body, code=200, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _redirect(self, url, extra=None):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _cookies(self):
        jar = {}
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
        return jar

    def _set_cookie(self, name, val, maxage=SESSION_TTL):
        return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax" % (name, val, maxage)

    def _clear_cookie(self, name):
        return "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" % name

    # ---------- 主路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # 1) 企业微信可信域名校验文件：必须免鉴权（企业微信服务器要能取到）
        #    形如 /WW_verify_xxxxxxxx.txt
        fname = path.lstrip("/")
        if fname.startswith("WW_verify_") and fname.endswith(".txt"):
            fpath = os.path.join(BASE_DIR, fname)
            if os.path.exists(fpath):
                try:
                    self._send(io.open(fpath, "rb").read(), 200, "text/plain; charset=utf-8")
                    return
                except Exception:
                    pass
            self._send(b"verify file not found", 404, "text/plain; charset=utf-8")
            return

        # 2) 健康检查（不泄露敏感信息）
        if path == "/healthz":
            self._send(json.dumps({"ok": True, "ts": int(time.time())}).encode(),
                       200, "application/json")
            return

        corp_id = env("QYWX_CORP_ID")
        secret = env("QYWX_AGENT_SECRET")
        if not corp_id or not secret:
            self._send(page_500("企业微信凭据未配置。"), 500)
            return
        key = get_session_key()

        # 3) 登出
        if path == "/logout":
            self._redirect("/", {"Set-Cookie": self._clear_cookie(SESSION_COOKIE)})
            return

        # 4) OAuth 回调
        if path == "/callback":
            self._handle_callback(qs, corp_id, secret, key)
            return

        # 5) 主页面：校验会话 -> 放行或跳转授权
        uid = check_session(self._cookies().get(SESSION_COOKIE, ""), key)
        if uid:
            allow = load_allowlist()
            if allow and uid not in allow:
                log("拒绝：UserId %s 不在白名单" % uid)
                self._send(page_403(uid), 403)
                return
            self._serve_html(uid)
            return

        # 无会话 -> 构造企业微信授权地址
        state = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
        redirect_uri = "%s://%s/callback" % (
            self.headers.get("X-Forwarded-Proto", "http"),
            self.headers.get("Host", "xnkqyy.cn"))
        auth_url = ("https://open.weixin.qq.com/connect/oauth2/authorize"
                    "?appid=%s&redirect_uri=%s&response_type=code"
                    "&scope=snsapi_base&state=%s#wechat_redirect") % (
            urllib.parse.quote(corp_id),
            urllib.parse.quote(redirect_uri, safe=""),
            urllib.parse.quote(state))
        log("未登录访问 %s -> 跳转企业微信授权" % path)
        self._redirect(auth_url)

    def _handle_callback(self, qs, corp_id, secret, key):
        code = (qs.get("code") or [""])[0]
        if not code:
            # 非企业微信客户端打开时，微信不会带 code
            log("回调缺少 code（可能非企业微信客户端打开）")
            self._send(page_wechat_only(), 403)
            return

        token = get_access_token(corp_id, secret)
        if not token:
            self._send(page_500("无法获取企业微信 access_token，请检查 Secret 与网络。"), 500)
            return

        userid, errmsg = get_userid_by_code(token, code)
        if not userid:
            log("换取 UserId 失败：%s" % errmsg)
            self._send(page_403(), 403)
            return

        allow = load_allowlist()
        if allow and userid not in allow:
            log("拒绝登录：UserId %s 不在白名单（共 %d 人）" % (userid, len(allow)))
            self._send(page_403(userid), 403)
            return

        if not allow:
            log("警告：白名单为空，已放行 UserId %s（建议尽快配置 allowlist.txt）" % userid)

        sid = make_session(userid, key)
        log("登录成功：UserId %s" % userid)
        self._redirect("/", {"Set-Cookie": self._set_cookie(SESSION_COOKIE, sid)})

    def _serve_html(self, uid):
        try:
            with io.open(HTML_PATH, "rb") as f:
                body = f.read()
            self._send(body, 200, "text/html; charset=utf-8",
                       {"X-Kanban-User": uid, "Cache-Control": "no-store"})
        except Exception as e:
            log("读取看板失败: %s" % e)
            self._send(page_500("看板文件读取失败：%s" % e), 500)

    def do_HEAD(self):
        self._send(b"", 200)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    missing = [k for k in ("QYWX_CORP_ID", "QYWX_AGENT_SECRET", "QYWX_AGENT_ID") if not env(k, False)]
    if missing:
        log("警告：以下环境变量未设置 -> %s（服务可启动，但访问会提示配置异常）" % ", ".join(missing))
    allow = load_allowlist()
    log("启动鉴权服务 port=%s 白名单=%d 人" % (PORT, len(allow)))
    srv = ThreadedServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("服务停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
