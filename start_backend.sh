#!/bin/bash

echo "Starting Interview Bot Backend API..."
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed"
    echo "Please install Python 3.8+ and try again"
    exit 1
fi

# Check if virtual environment exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

# Check if requirements are installed
python3 -c "import fastapi" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

echo ""
echo "Starting FastAPI server on http://localhost:8000"
echo "Press Ctrl+C to stop the server"
echo ""

python3 interview_api.py

