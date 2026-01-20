# NCS - New Control System

A modern, unified control system for the ALS (Advanced Light Source) facility.

## Overview

NCS provides a Qt-based application for scientific data acquisition and hardware controls,
featuring:

- Facility-wide consistency with beamline-specific customization
- API-first, modular architecture enabling extensibility
- LLM/AI integration for user assistance and automation
- FAIR-compliant data management
- Secure remote operations

## Installation

### Development Installation

```bash
# Clone the repository
git clone https://github.com/als-computing/ncs.git
cd ncs

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# or
source .venv/bin/activate  # Unix

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

### Running the Application

```bash
ncs
```

## Development

### Running Tests

```bash
pytest
```

### Code Quality

```bash
# Linting
ruff check src tests

# Type checking
mypy src
```

## License

BSD-3-Clause
