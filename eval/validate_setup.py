#!/usr/bin/env python3
"""
Validation script to check eval setup before running.

Usage: python eval/validate_setup.py
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple

def check_environment_variables() -> List[Tuple[str, bool, str]]:
    """Check required environment variables."""
    checks = []
    
    # Required
    checks.append((
        "EVAL_API_TOKEN",
        bool(os.getenv("EVAL_API_TOKEN")),
        "Required for API authentication. Get a JWT token from the app."
    ))
    
    checks.append((
        "LLM_API_KEY",
        bool(os.getenv("LLM_API_KEY")),
        "Required for LLM API calls (e.g., OpenAI API key)."
    ))
    
    # Optional but recommended
    checks.append((
        "LANGFUSE_PUBLIC_KEY",
        bool(os.getenv("LANGFUSE_PUBLIC_KEY")),
        "Optional: For detailed cost tracking and observability."
    ))
    
    checks.append((
        "LANGFUSE_SECRET_KEY",
        bool(os.getenv("LANGFUSE_SECRET_KEY")),
        "Optional: For detailed cost tracking and observability."
    ))
    
    return checks


def check_test_data() -> List[Tuple[str, bool, str]]:
    """Check for labeled test data."""
    checks = []
    
    test_dir = Path("data/synthetic/test")
    train_dir = Path("data/synthetic/train")
    
    test_exists = test_dir.exists()
    train_exists = train_dir.exists()
    
    test_files = list(test_dir.glob("*.json")) if test_exists else []
    train_files = list(train_dir.glob("*.json")) if train_exists else []
    
    checks.append((
        "Test data directory",
        test_exists,
        f"data/synthetic/test/ {'exists' if test_exists else 'missing'}"
    ))
    
    checks.append((
        "Test data files",
        len(test_files) > 0,
        f"Found {len(test_files)} labeled JSON files in test/"
    ))
    
    checks.append((
        "Train data files",
        len(train_files) > 0,
        f"Found {len(train_files)} labeled JSON files in train/ (optional)"
    ))
    
    # Validate test files reference existing documents
    valid_files = 0
    invalid_files = []
    
    for json_file in test_files:
        try:
            import json
            with open(json_file) as f:
                data = json.load(f)
                file_path = data.get("file_path")
                if file_path and Path(file_path).exists():
                    valid_files += 1
                else:
                    invalid_files.append((json_file.name, file_path))
        except Exception as e:
            invalid_files.append((json_file.name, str(e)))
    
    checks.append((
        "Valid test files",
        valid_files > 0 and len(invalid_files) == 0,
        f"{valid_files} files reference existing documents. "
        f"{len(invalid_files)} files have missing/invalid references."
    ))
    
    if invalid_files:
        for name, issue in invalid_files:
            checks.append((
                f"  ↳ {name}",
                False,
                f"Issue: {issue}"
            ))
    
    return checks


def check_api_connectivity() -> List[Tuple[str, bool, str]]:
    """Check API connectivity."""
    checks = []
    
    api_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    
    try:
        import requests
        response = requests.get(f"{api_url}/api/v1/health", timeout=5)
        is_healthy = response.status_code == 200
        
        checks.append((
            "API health check",
            is_healthy,
            f"{api_url}/api/v1/health returned {response.status_code}"
        ))
    except Exception as e:
        checks.append((
            "API connectivity",
            False,
            f"Failed to connect to {api_url}: {e}"
        ))
    
    return checks


def main():
    """Run all validation checks."""
    print("=" * 80)
    print("Cargo Copilot Evaluation Setup Validation")
    print("=" * 80)
    print()
    
    all_checks = []
    
    # Environment variables
    print("📋 Environment Variables")
    print("-" * 80)
    env_checks = check_environment_variables()
    all_checks.extend(env_checks)
    
    for name, passed, description in env_checks:
        status = "✅" if passed else "❌"
        print(f"{status} {name:25s} {description}")
    print()
    
    # Test data
    print("📁 Test Data")
    print("-" * 80)
    data_checks = check_test_data()
    all_checks.extend(data_checks)
    
    for name, passed, description in data_checks:
        status = "✅" if passed else "❌"
        indent = "  " if name.startswith("  ↳") else ""
        print(f"{indent}{status} {name:25s} {description}")
    print()
    
    # API connectivity
    print("🌐 API Connectivity")
    print("-" * 80)
    api_checks = check_api_connectivity()
    all_checks.extend(api_checks)
    
    for name, passed, description in api_checks:
        status = "✅" if passed else "❌"
        print(f"{status} {name:25s} {description}")
    print()
    
    # Summary
    print("=" * 80)
    total_checks = len(all_checks)
    passed_checks = sum(1 for _, passed, _ in all_checks if passed)
    
    # Required checks (not including optional Langfuse)
    required_check_names = [
        "EVAL_API_TOKEN", "LLM_API_KEY", "Test data directory", 
        "Test data files", "Valid test files", "API health check"
    ]
    required_checks = [
        (name, passed) 
        for name, passed, _ in all_checks 
        if name in required_check_names
    ]
    required_passed = sum(1 for _, passed in required_checks if passed)
    required_total = len(required_checks)
    
    if required_passed == required_total:
        print(f"✅ READY: All required checks passed ({required_passed}/{required_total})")
        print()
        print("You can now run: make eval")
        return_code = 0
    else:
        print(f"❌ NOT READY: {required_total - required_passed} required checks failed")
        print()
        print("Fix the issues above before running eval.")
        print()
        print("Quick setup guide:")
        print("  1. Set EVAL_API_TOKEN (get from app login)")
        print("  2. Set LLM_API_KEY (your OpenAI API key)")
        print("  3. Create labeled data in data/synthetic/test/")
        print("  4. Ensure API is running (docker compose up)")
        print()
        print("See eval/README.md for detailed instructions.")
        return_code = 1
    
    print()
    print(f"Optional checks: {passed_checks - required_passed}/{total_checks - required_total} passed")
    print("=" * 80)
    
    sys.exit(return_code)


if __name__ == "__main__":
    main()
