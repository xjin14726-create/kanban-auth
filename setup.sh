#!/usr/bin/env bash
# ---------------------------------------------------------------
# 内部数据看板 - 企业微信鉴权服务 一键部署脚本
# 在阿里云 ECS (Ubuntu 22.04) 上以 root 执行：
#   bash setup.sh
#
# 本脚本只做「环境搭建 + 服务注册」，不写入任何真实凭据。
# 凭据由管理员自行填写 /opt/xnkq-kanban/kanban.env（权限 600）。
# ---------------------------------------------------------------
set -e

APP_DIR=/opt/xnkq-kanban
SVC_NAME=kanban-auth
PORT=8899
DOMAIN=yunying.xnkq.net
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=============================================="
echo " 内部数据看板 · 企业微信鉴权服务 部署"
echo "=============================================="

# ---------- 1. 基础检查 ----------
if [ "$(id -u)" != "0" ]; then
  echo "[错误] 请以 root 执行：sudo bash setup.sh"
  exit 1
fi
echo "[1/7] 基础检查"
command -v python3 >/dev/null 2>&1 || { echo "[错误] 未找到 python3"; exit 1; }
echo "      python3: $(python3 --version 2>&1)"

# ---------- 2. 创建目录 ----------
echo "[2/7] 准备目录 $APP_DIR"
mkdir -p "$APP_DIR/logs"
cp -f "$SCRIPT_DIR/auth_server.py" "$APP_DIR/" 2>/dev/null || echo "      (auth_server.py 已存在或被单独上传，跳过)"

# ---------- 3. 凭据文件（不覆盖已存在的）----------
echo "[3/7] 凭据文件"
if [ ! -f "$APP_DIR/kanban.env" ]; then
  cp -f "$SCRIPT_DIR/kanban.env.example" "$APP_DIR/kanban.env"
  chmod 600 "$APP_DIR/kanban.env"
  echo "      已生成 $APP_DIR/kanban.env（权限 600）<- 请填写真实凭据"
else
  chmod 600 "$APP_DIR/kanban.env"
  echo "      已存在 kanban.env，保留不覆盖"
fi

# ---------- 4. 白名单 ----------
echo "[4/7] 白名单"
if [ ! -f "$APP_DIR/allowlist.txt" ]; then
  cp -f "$SCRIPT_DIR/allowlist.txt" "$APP_DIR/allowlist.txt"
  echo "      已生成 allowlist.txt"
else
  echo "      已存在 allowlist.txt，保留不覆盖"
fi

# ---------- 5. Nginx ----------
echo "[5/7] Nginx"
if ! command -v nginx >/dev/null 2>&1; then
  echo "      未安装，正在安装..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq nginx
else
  echo "      已安装：$(nginx -v 2>&1 | head -1)"
fi
cp -f "$SCRIPT_DIR/nginx-yunying-kanban.conf" /etc/nginx/sites-available/yunying-kanban
ln -sf /etc/nginx/sites-available/yunying-kanban /etc/nginx/sites-enabled/yunying-kanban
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && echo "      Nginx 配置校验通过"
systemctl enable nginx >/dev/null 2>&1
systemctl restart nginx
echo "      Nginx 已重启"

# ---------- 6. systemd 服务 ----------
echo "[6/7] 注册系统服务"
cp -f "$SCRIPT_DIR/kanban-auth.service" /etc/systemd/system/$SVC_NAME.service
systemctl daemon-reload
systemctl enable $SVC_NAME >/dev/null 2>&1
echo "      服务已注册（先不要启动，凭据填好后再启动）"

# ---------- 7. 防火墙提示 ----------
echo "[7/7] 完成"
echo ""
echo "=============================================="
echo " 部署步骤已完成，剩余操作："
echo ""
echo " 1) 填写企业微信凭据："
echo "      vi $APP_DIR/kanban.env"
echo "    填写 QYWX_CORP_ID / QYWX_AGENT_SECRET / QYWX_AGENT_ID"
echo ""
echo " 2) 配置访问白名单（UserId，一行一个）："
echo "      vi $APP_DIR/allowlist.txt"
echo ""
echo " 3) 上传看板 HTML 到："
echo "      $APP_DIR/index.html"
echo ""
echo " 4) 上传企业微信可信域名校验文件到同目录"
echo "      （形如 WW_verify_xxxxxxxx.txt）"
echo ""
echo " 5) 启动服务："
echo "      systemctl start $SVC_NAME"
echo "      systemctl status $SVC_NAME"
echo ""
echo " 6) 确认阿里云安全组已放行 80 端口（HTTPS 另需 443）"
echo ""
echo " 日志：$APP_DIR/logs/auth.log"
echo "       journalctl -u $SVC_NAME -f"
echo "=============================================="
