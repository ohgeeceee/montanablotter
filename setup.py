#!/usr/bin/env python3
"""
Montana Blotter - Quick Setup Script
Automates the deployment process
"""

import os
import sys
import subprocess
from pathlib import Path

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_step(step_num, message):
    print(f"\n{Colors.BOLD}[Step {step_num}]{Colors.END} {message}")

def print_success(message):
    print(f"{Colors.GREEN}✓{Colors.END} {message}")

def print_warning(message):
    print(f"{Colors.YELLOW}⚠{Colors.END} {message}")

def print_error(message):
    print(f"{Colors.RED}✗{Colors.END} {message}")

def run_command(cmd, description):
    """Run a shell command and return success status"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print_success(description)
            return True
        else:
            print_error(f"{description} failed: {result.stderr}")
            return False
    except Exception as e:
        print_error(f"{description} failed: {str(e)}")
        return False

def main():
    print(f"\n{Colors.BOLD}{'='*60}")
    print("MONTANA BLOTTER - AUTOMATED SETUP")
    print(f"{'='*60}{Colors.END}\n")
    
    base_dir = Path("/root/montanablotter")
    
    # Step 1: Check Python version
    print_step(1, "Checking Python version...")
    if sys.version_info < (3, 7):
        print_error("Python 3.7 or higher required")
        sys.exit(1)
    print_success(f"Python {sys.version.split()[0]} detected")
    
    # Step 2: Check dependencies
    print_step(2, "Checking dependencies...")
    required_packages = ['flask', 'flask_login', 'flask_bcrypt', 'pdfplumber']
    missing = []
    
    for package in required_packages:
        try:
            __import__(package.replace('_', ''))
            print_success(f"{package} installed")
        except ImportError:
            missing.append(package)
            print_warning(f"{package} not found")
    
    if missing:
        print("\nInstalling missing packages...")
        run_command(f"pip3 install {' '.join(missing)}", "Package installation")
    
    # Step 3: Create directories
    print_step(3, "Creating directories...")
    directories = [
        base_dir / "uploads",
        base_dir / "records",
        base_dir / "templates",
        base_dir / "static"
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        print_success(f"Created {directory}")
    
    # Step 4: Initialize database
    print_step(4, "Initializing database...")
    if run_command("python3 init_db.py", "Database initialization"):
        pass
    else:
        print_error("Database initialization failed. Check if init_db.py exists.")
    
    # Step 5: Create admin user
    print_step(5, "Creating admin user...")
    print("Provide admin password via prompt, or set MB_ADMIN_BOOTSTRAP_PASSWORD.")
    run_command("python3 seed_admin.py admin", "Admin user creation")
    
    # Step 6: Test PDF parser
    print_step(6, "Testing PDF parser...")
    test_pdf = base_dir / "uploads" / "your_file.pdf"
    if test_pdf.exists():
        run_command(f"python3 pdf_parser.py {test_pdf}", "PDF parser test")
    else:
        print_warning("No test PDF found. Upload a PDF to uploads/ and test manually.")
    
    # Step 7: Setup instructions
    print_step(7, "Setup complete!")
    
    print(f"\n{Colors.BOLD}NEXT STEPS:{Colors.END}")
    print("1. Create .env from .env.example and set credentials")
    print("2. Test the Flask app: python3 app.py")
    print("3. Set up gunicorn systemd service (see DEPLOYMENT_GUIDE.py)")
    print("4. Configure nginx")
    print("5. Set up cron job for email worker")
    
    print(f"\n{Colors.BOLD}QUICK START:{Colors.END}")
    print("  # Test the app")
    print("  python3 app.py")
    print("")
    print("  # Process a PDF manually")
    print("  python3 processor.py uploads/file.pdf CountyName")
    print("")
    print("  # Run email worker")
    print("  python3 email_worker.py")
    
    print(f"\n{Colors.GREEN}Setup completed successfully!{Colors.END}\n")

if __name__ == "__main__":
    main()
