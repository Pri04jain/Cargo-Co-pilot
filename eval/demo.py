#!/usr/bin/env python3
"""
DEPRECATED: Demo script with simulated data.

This script is for demonstration purposes only and uses fake data.
For real evaluation, use: make eval

Real evaluation requires:
- Labeled documents in data/synthetic/test/ or data/synthetic/train/
- Running API with valid LLM_API_KEY
- EVAL_API_TOKEN for authentication

Usage (for demo purposes only): python eval/demo.py
"""

import sys

def main():
    """Print deprecation notice."""
    print("=" * 80)
    print("DEPRECATED: This demo script uses simulated data and fake results.")
    print("=" * 80)
    print()
    print("For real evaluation with actual pipeline and LLM calls, use:")
    print()
    print("  make eval")
    print()
    print("Prerequisites:")
    print("  1. Create labeled test data in data/synthetic/test/")
    print("  2. Set LLM_API_KEY environment variable")
    print("  3. Set EVAL_API_TOKEN environment variable")
    print("  4. Ensure API is running (docker compose up)")
    print()
    print("The eval system now uses the real pipeline and computes metrics from")
    print("actual LLM outputs, not simulated results.")
    print()
    sys.exit(1)

if __name__ == "__main__":
    main()