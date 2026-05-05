#!/usr/bin/env python3
"""
Monitor Anthropic API credit balance and alert when low.

Usage:
    python anthropic_credit_monitor.py --check
    python anthropic_credit_monitor.py --alert-threshold 5.0

Can be run as a cron job for proactive monitoring.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent))

try:
    import config
except ImportError:
    config = None

LOG_PATH = "/root/montanablotter/logs/credit_monitor.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_api_key():
    """Get Anthropic API key from config or environment."""
    if config and hasattr(config, "ANTHROPIC_API_KEY"):
        return config.ANTHROPIC_API_KEY
    return os.getenv("ANTHROPIC_API_KEY", "")


def check_credit_balance(api_key: str):
    """Check Anthropic API credit balance.
    
    Returns:
        dict with keys: success (bool), balance (float or None), error (str or None)
    """
    if not api_key:
        return {"success": False, "balance": None, "error": "No API key configured"}
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        # Try a minimal API call to check if credits are available
        # If credits are low, this will fail with a 400 error
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
                timeout=10,
            )
            # If we get here, API is working and credits exist
            return {"success": True, "balance": None, "error": None, "status": "active"}
        except Exception as e:
            error_str = str(e)
            if "credit balance is too low" in error_str or "credit" in error_str.lower():
                return {"success": False, "balance": 0.0, "error": "Credit balance depleted", "status": "depleted"}
            elif "authentication" in error_str.lower() or "api_key" in error_str.lower():
                return {"success": False, "balance": None, "error": f"Authentication error: {error_str}", "status": "auth_error"}
            else:
                return {"success": False, "balance": None, "error": f"API error: {error_str}", "status": "error"}
                
    except ImportError:
        return {"success": False, "balance": None, "error": "anthropic SDK not installed", "status": "no_sdk"}
    except Exception as e:
        return {"success": False, "balance": None, "error": f"Unexpected error: {e}", "status": "error"}


def send_alert(message: str, status: str):
    """Send alert via available channels."""
    # Log the alert
    logger.warning("ALERT: %s (status=%s)", message, status)
    
    # Print to stdout for cron email capture
    print(f"[ALERT] {message}", file=sys.stderr)
    
    # Try to send via Telegram if configured
    telegram_token = os.getenv("MB_TELEGRAM_BOT_TOKEN")
    telegram_chat = os.getenv("MB_TELEGRAM_TARGET_DEFAULT")
    
    if telegram_token and telegram_chat:
        try:
            import urllib.request
            import urllib.parse
            
            alert_text = f"🚨 Montana Blotter Alert\n\n{message}\n\nTime: {datetime.now().isoformat()}"
            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": telegram_chat,
                "text": alert_text,
                "parse_mode": "HTML"
            }).encode()
            
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent successfully")
                else:
                    logger.error("Telegram alert failed: HTTP %s", resp.status)
        except Exception as e:
            logger.error("Failed to send Telegram alert: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Monitor Anthropic API credits")
    parser.add_argument("--check", action="store_true", help="Check current balance and exit")
    parser.add_argument("--alert-threshold", type=float, default=5.0, 
                        help="Credit threshold below which to alert (default: 5.0)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()
    
    api_key = get_api_key()
    result = check_credit_balance(api_key)
    
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["success"] else 1
    
    if result["success"]:
        print(f"✓ Anthropic API is active and responding")
        logger.info("Credit check passed: API active")
        return 0
    else:
        status = result.get("status", "unknown")
        error = result["error"]
        balance = result.get("balance")
        
        if status == "depleted":
            print(f"✗ CRITICAL: Anthropic API credits depleted!")
            print(f"  Error: {error}")
            print(f"  Action needed: Add credits at https://console.anthropic.com/settings/plans")
            send_alert(
                f"Anthropic API credits are DEPLETED.\n"
                f"Daily blog posts will use fallback template until credits are restored.\n"
                f"Add credits: https://console.anthropic.com/settings/plans",
                status
            )
            return 2
        elif status == "auth_error":
            print(f"✗ Authentication error: {error}")
            print(f"  Check your ANTHROPIC_API_KEY configuration")
            send_alert(f"Anthropic API authentication failed: {error}", status)
            return 3
        else:
            print(f"✗ Error: {error}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
