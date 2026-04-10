#!/usr/bin/env python3
"""Test the stock screening API via HTTP requests."""

import json
import sys
from urllib.parse import urljoin

import httpx


def test_stock_screening_api():
    """Test the stock screening UI API endpoints."""
    base_url = "http://localhost:7860"
    
    print(f"Testing Stock Screening API at {base_url}\n")
    
    with httpx.Client(timeout=30.0) as client:
        # Test 1: Check if server is running
        print("1. Testing server health...")
        try:
            response = client.get(base_url)
            if response.status_code == 200:
                print(f"   ✓ Server is running (status: {response.status_code})")
                print(f"   ✓ Page title found: {response.text[:100]}...")
            else:
                print(f"   ✗ Unexpected status: {response.status_code}")
                return False
        except Exception as e:
            print(f"   ✗ Server not accessible: {e}")
            return False
        
        # Test 2: Check API endpoint (if available)
        print("\n2. Testing API endpoints...")
        api_url = urljoin(base_url, "/api/chat")
        try:
            # Try to find if there's an API endpoint
            response = client.get(api_url)
            print(f"   API endpoint status: {response.status_code}")
        except Exception as e:
            print(f"   API endpoint not available (expected for Gradio UI): {e}")
        
        # Test 3: Check Gradio API
        print("\n3. Testing Gradio API...")
        gradio_api = urljoin(base_url, "/api/")
        try:
            response = client.get(gradio_api)
            if response.status_code == 200:
                print(f"   ✓ Gradio API accessible")
                data = response.json()
                print(f"   ✓ API info: {json.dumps(data, indent=2)[:200]}...")
            else:
                print(f"   Status: {response.status_code}")
        except Exception as e:
            print(f"   ✗ Gradio API error: {e}")
        
        # Test 4: Check if we can get the page content
        print("\n4. Analyzing page content...")
        try:
            response = client.get(base_url)
            content = response.text.lower()
            
            checks = [
                ("stock", "stock" in content or "screening" in content),
                ("chat", "chat" in content or "message" in content),
                ("gradio", "gradio" in content),
            ]
            
            for check_name, result in checks:
                status = "✓" if result else "✗"
                print(f"   {status} {check_name.capitalize()} found in page")
            
        except Exception as e:
            print(f"   ✗ Error analyzing content: {e}")
        
        print("\n" + "="*60)
        print("✓ Basic connectivity test completed!")
        print("="*60)
        print("\nTo fully test the UI:")
        print("1. Open http://localhost:7860 in your browser")
        print("2. Enter a query like: 'Find 3 value stocks with low P/E'")
        print("3. Click Send and wait for the response")
        print("\nNote: Make sure API keys are configured in .env file")
        
    return True


if __name__ == "__main__":
    success = test_stock_screening_api()
    sys.exit(0 if success else 1)
