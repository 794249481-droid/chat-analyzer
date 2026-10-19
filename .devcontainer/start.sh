Warning: fs was declared with const; use let for reassignable variables.
#!/bin/bash
cd "$(dirname "$0")/.."

echo "启动销售客服分析系统..."
kill $(lsof -ti:8888) 2>/dev/null
sleep 1

nohup python3 server.py > /tmp/server.log 2>&1 &
sleep 2

echo ""
echo "========================================"
echo "  服务已启动!"
echo "  端口: 8888"
echo "  本地: http://localhost:8888"
echo ""
echo "  公网地址见 Codespaces 端口面板"
echo "  (点击右下角端口 → 右键 8888 → 可见性改为 public)"
echo "========================================"
