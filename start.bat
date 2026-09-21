@echo off
echo 🇮🇳 CitizenVoice India - Starting Prototype
echo ============================================

if not exist venv (
    echo 📦 Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo 📥 Installing dependencies...
pip install -r requirements.txt

echo 🚀 Starting server on http://localhost:5000
echo.
echo Open these URLs in your browser:
echo   🏠 Home:       http://localhost:5000
echo   📝 Submit:     http://localhost:5000/submit
echo   📊 Dashboard:  http://localhost:5000/dashboard
echo.
python app.py
