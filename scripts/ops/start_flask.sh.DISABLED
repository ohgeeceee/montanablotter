#!/bin/bash
# Start Montana Blotter Flask App

echo "🚀 Starting Montana Blotter..."
echo ""

cd /root/montanablotter

# Kill any existing Flask processes
echo "Stopping old processes..."
pkill -f app.py
pkill -f gunicorn
sleep 2

# Check if database exists
if [ ! -f "blotter.db" ]; then
    echo "📊 Creating database..."
    python3 init_db.py
    python3 seed_admin.py
fi

# Test for import errors
echo "🧪 Testing app.py..."
python3 -c "import app" 2>&1
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ ERROR: app.py has errors!"
    echo "Fix the errors above before proceeding."
    exit 1
fi

echo ""
echo "✅ Starting Flask on http://0.0.0.0:5000"
echo "   Access your site at: http://your-server-ip:5000"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start Flask
python3 app.py
