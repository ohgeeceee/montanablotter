#!/bin/bash
# Montana Blotter - 502 Error Quick Fix

echo "🔍 DIAGNOSING 502 ERROR..."
echo ""

# Check 1: Is Flask running?
echo "1️⃣ Checking if Flask/Gunicorn is running..."
if pgrep -f "app.py" > /dev/null || pgrep -f "gunicorn" > /dev/null; then
    echo "✅ Process found:"
    ps aux | grep -E "app.py|gunicorn" | grep -v grep
else
    echo "❌ Flask is NOT running - This is your problem!"
    echo ""
    echo "QUICK FIX:"
    echo "  cd /root/montanablotter"
    echo "  python3 app.py"
    echo ""
    exit 1
fi

echo ""
echo "2️⃣ Testing if app.py has errors..."
cd /root/montanablotter
python3 -c "import app" 2>&1
if [ $? -eq 0 ]; then
    echo "✅ No import errors"
else
    echo "❌ Import errors found - Fix these first!"
    echo ""
    echo "Common fixes:"
    echo "  - Make sure all .py files are in /root/montanablotter/"
    echo "  - Run: python3 init_db.py"
    echo "  - Check: ls -l *.py"
    exit 1
fi

echo ""
echo "3️⃣ Checking what's listening on ports..."
echo "Port 5000 (Flask should be here):"
netstat -tlnp 2>/dev/null | grep :5000 || echo "  Nothing on port 5000"
echo ""
echo "Port 80 (nginx):"
netstat -tlnp 2>/dev/null | grep :80 || echo "  Nothing on port 80"

echo ""
echo "4️⃣ Checking nginx error logs..."
tail -5 /var/log/nginx/error.log

echo ""
echo "================================"
echo "DIAGNOSIS COMPLETE"
echo "================================"
