#!/bin/bash
# CorvinOS Premium Video Generator Setup Script
# Installs all dependencies and prepares the environment

set -e

echo "╔══════════════════════════════════════════════════════════════════════════╗"
echo "║            CorvinOS Premium Video Generator - Setup Script               ║"
echo "╚══════════════════════════════════════════════════════════════════════════╝"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}✗ Python 3.8+ required (found $PYTHON_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"
echo ""

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check required tools
echo -e "${YELLOW}Checking required tools...${NC}"

REQUIRED_TOOLS=("ffmpeg" "python3")
for tool in "${REQUIRED_TOOLS[@]}"; do
    if command_exists "$tool"; then
        VERSION=$($tool --version 2>&1 | head -n1)
        echo -e "${GREEN}✓ $tool${NC} - $VERSION"
    else
        echo -e "${RED}✗ $tool is required but not installed${NC}"
        echo "  Install with: sudo apt-get install $tool"
        exit 1
    fi
done

# Check for pip (more flexible)
echo -n "Checking pip... "
if command_exists "pip3"; then
    echo -e "${GREEN}✓ pip3${NC}"
elif command_exists "pip"; then
    echo -e "${GREEN}✓ pip${NC}"
elif python3 -m pip --version >/dev/null 2>&1; then
    echo -e "${GREEN}✓ python3 -m pip${NC}"
else
    echo -e "${RED}✗ pip is required${NC}"
    echo "  Install with: sudo apt-get install python3-pip"
    exit 1
fi
echo ""

# Check optional tools
echo -e "${YELLOW}Checking optional tools (for enhanced quality)...${NC}"

OPTIONAL_TOOLS=("blender" "manim" "convert" "inkscape")
for tool in "${OPTIONAL_TOOLS[@]}"; do
    if command_exists "$tool"; then
        VERSION=$($tool --version 2>&1 | head -n1)
        echo -e "${GREEN}✓ $tool${NC} - $VERSION"
    else
        echo -e "${YELLOW}⊘ $tool${NC} (optional, fallback available)"
    fi
done
echo ""

# Create virtual environment (optional but recommended)
echo -e "${YELLOW}Setting up Python environment...${NC}"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip3 install -q -r requirements.txt
echo -e "${GREEN}✓ Python dependencies installed${NC}"
echo ""

# Create necessary directories
echo -e "${YELLOW}Creating output directories...${NC}"
mkdir -p output/assets
mkdir -p output/frames
mkdir -p logs
mkdir -p config
echo -e "${GREEN}✓ Directories created${NC}"
echo ""

# Verify configuration file
if [ ! -f "config/video_settings.yaml" ]; then
    echo -e "${YELLOW}Note: config/video_settings.yaml not found${NC}"
    echo "      A default configuration will be created from template"
fi
echo ""

# Make scripts executable
echo -e "${YELLOW}Setting permissions...${NC}"
chmod +x corvin_video_generator.py
echo -e "${GREEN}✓ Scripts are executable${NC}"
echo ""

echo "╔══════════════════════════════════════════════════════════════════════════╗"
echo "║                         Setup Complete! ✓                               ║"
echo "╠══════════════════════════════════════════════════════════════════════════╣"
echo "║                                                                          ║"
echo "║  Next steps:                                                            ║"
echo "║                                                                          ║"
echo "║  1. Review configuration:                                              ║"
echo "║     $ cat config/video_settings.yaml                                    ║"
echo "║                                                                          ║"
echo "║  2. Check dependencies:                                                ║"
echo "║     $ python3 corvin_video_generator.py --check-deps                    ║"
echo "║                                                                          ║"
echo "║  3. Generate complete video:                                           ║"
echo "║     $ python3 corvin_video_generator.py                                 ║"
echo "║                                                                          ║"
echo "║  4. Run specific phase:                                                ║"
echo "║     $ python3 corvin_video_generator.py --phase 1                       ║"
echo "║                                                                          ║"
echo "║  For more help: python3 corvin_video_generator.py --help               ║"
echo "║                                                                          ║"
echo "╚══════════════════════════════════════════════════════════════════════════╝"
echo ""
