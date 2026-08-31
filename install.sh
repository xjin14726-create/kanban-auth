#!/usr/bin/env bash
# ---------------------------------------------------------------
# 一键安装：从 GitHub 拉取鉴权服务文件并完成服务器环境搭建
# 在 ECS 上以 root 执行：
#   curl -fsSL https://cdn.jsdelivr.net/gh/xjin14726-create/kanban-auth@v1.0.0/install.sh | bash
# ---------------------------------------------------------------
set -e

# 用 jsDelivr CDN 下载（固定 Tag，从 jsDelivr 自有 CDN 完整分发，大文件不截断、安装可复现）
CDN="https://cdn.jsdelivr.net/gh"
AUTH_REPO="$CDN/xjin14726-create/kanban-auth@v1.0.0"
APP_DIR=/opt/xnkq-kanban
TMP_DIR=/tmp/kanban-auth-$$

echo "=============================================="
echo " 内部数据看板 · 企业微信鉴权 一键安装"
echo "=============================================="

if [ "$(id -u)" != "0" ]; then
  echo "[错误] 请用 root 执行（先运行 sudo -i）"
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "[错误] 未找到 python3"; exit 1; }

echo "[1/6] 下载鉴权服务文件"
mkdir -p "$TMP_DIR"
for f in auth_server.py nginx-kanban.conf kanban-auth.service \
         kanban.env.example allowlist.txt setup.sh; do
  curl -fsSL "$AUTH_REPO/$f" -o "$TMP_DIR/$f" && echo "      √ $f"
done

echo "[1.5/6] 下载看板页面"
curl -fsSL "$AUTH_REPO/index.html" -o "$TMP_DIR/index.html" \
  && echo "      √ index.html ($(stat -c%s "$TMP_DIR/index.html") 字节)" \
  || echo "      ! 看板下载失败，稍后手动补传"

echo "[2/6] 准备目录 $APP_DIR"
mkdir -p "$APP_DIR/logs"

echo "[3/6] 安装鉴权服务文件"
cp -f "$TMP_DIR/auth_server.py" "$APP_DIR/"
chmod 755 "$APP_DIR/auth_server.py"

echo "[4/6] 凭据文件"
if [ ! -f "$APP_DIR/kanban.env" ]; then
  cp -f "$TMP_DIR/kanban.env.example" "$APP_DIR/kanban.env"
  echo "      √ 已生成 kanban.env（稍后填写）"
else
  echo "      - kanban.env 已存在，保留"
fi
chmod 600 "$APP_DIR/kanban.env"

echo "[5/6] 看板页面 + 白名单"
[ -f "$APP_DIR/index.html" ] || cp -f "$TMP_DIR/index.html" "$APP_DIR/" 2>/dev/null
if [ -f "$APP_DIR/index.html" ]; then
  echo "      √ index.html ($(stat -c%s "$APP_DIR/index.html") 字节)"
else
  echo "      ! 缺少 index.html，需手动上传到 $APP_DIR/"
fi
[ -f "$APP_DIR/allowlist.txt" ] || cp -f "$TMP_DIR/allowlist.txt" "$APP_DIR/"
echo "      √ allowlist.txt 就绪"

echo "[6/6] Nginx + systemd"
if ! command -v nginx >/dev/null 2>&1; then
  echo "      安装 nginx..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq nginx
fi
cp -f "$TMP_DIR/nginx-kanban.conf" /etc/nginx/sites-available/kanban
ln -sf /etc/nginx/sites-available/kanban /etc/nginx/sites-enabled/kanban
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && echo "      √ Nginx 配置校验通过"
systemctl enable nginx >/dev/null 2>&1
systemctl restart nginx && echo "      √ Nginx 已启动"

cp -f "$TMP_DIR/kanban-auth.service" /etc/systemd/system/kanban-auth.service
systemctl daemon-reload
systemctl enable kanban-auth >/dev/null 2>&1
echo "      √ 系统服务已注册（凭据填好后启动）"

rm -rf "$TMP_DIR"

echo ""
echo "=============================================="
echo " 环境搭建完成！剩余 3 步："
echo ""
echo " ① 填写企业微信凭据："
echo "      vi $APP_DIR/kanban.env"
echo "    改 3 行：QYWX_CORP_ID / QYWX_AGENT_SECRET / QYWX_AGENT_ID"
echo ""
echo " ② 上传看板 HTML 与域名校验文件到 $APP_DIR/"
echo "     （index.html + WW_verify_xxxx.txt）"
echo ""
echo " ③ 启动服务："
echo "      systemctl start kanban-auth"
echo "      systemctl status kanban-auth"
echo ""
echo " 日志：tail -f $APP_DIR/logs/auth.log"
echo "=============================================="
