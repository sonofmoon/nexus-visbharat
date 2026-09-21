#!/bin/bash
# CitizenVoice India - Quick Start Script

echo "🇮🇳 CitizenVoice India - Starting Prototype"
echo "============================================"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.10+"
    exit 1
fi

# Create virtual environment if not exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Run
echo "🚀 Starting server on http://localhost:5000"
echo ""
echo "Open these URLs in your browser:"
echo "  🏠 Home:       http://localhost:5000"
echo "  📝 Submit:     http://localhost:5000/submit"
echo "  📊 Dashboard:  http://localhost:5000/dashboard"
echo ""
python app.py
