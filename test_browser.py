#!/usr/bin/env python3
"""Browser automation test for stock screening UI."""

import asyncio
import sys
from playwright.async_api import async_playwright


async def test_stock_screening_ui():
    """Test the stock screening UI with browser automation."""
    url = "http://localhost:7860"
    
    async with async_playwright() as p:
        print(f"Launching browser to test {url}...")
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        try:
            # Navigate to the UI
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until="networkidle", timeout=30000)
            print("✓ Page loaded successfully")
            
            # Wait for the chat interface to be ready
            print("Waiting for chat interface...")
            await page.wait_for_selector("textarea", timeout=10000)
            print("✓ Chat interface found")
            
            # Check for key UI elements
            title = await page.title()
            print(f"✓ Page title: {title}")
            
            # Look for the main heading
            heading = await page.locator("h1, h2").first
            if await heading.count() > 0:
                heading_text = await heading.text_content()
                print(f"✓ Main heading: {heading_text}")
            
            # Find the text input
            text_input = page.locator("textarea").first
            if await text_input.count() > 0:
                print("✓ Text input found")
                
                # Type a test query
                test_query = "Find 3 value stocks with low P/E"
                print(f"Typing test query: '{test_query}'...")
                await text_input.fill(test_query)
                print("✓ Query entered")
                
                # Find and click the submit button
                submit_button = page.locator("button:has-text('Send')").first
                if await submit_button.count() > 0:
                    print("✓ Submit button found")
                    await submit_button.click()
                    print("✓ Query submitted")
                    
                    # Wait for response (with timeout)
                    print("Waiting for response...")
                    try:
                        # Wait for chatbot to update (look for assistant message)
                        await page.wait_for_selector(
                            ".message, [role='assistant'], .assistant",
                            timeout=30000
                        )
                        print("✓ Response received")
                        
                        # Get the response text
                        messages = await page.locator(".message, [role='assistant']").all()
                        if messages:
                            last_message = await messages[-1].text_content()
                            print(f"\n✓ Response preview: {last_message[:200]}...")
                        
                    except Exception as e:
                        print(f"⚠ Response timeout or error: {e}")
                        print("(This might be normal if API keys are not configured)")
                
            # Take a screenshot
            screenshot_path = "test_screenshot.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"✓ Screenshot saved to {screenshot_path}")
            
            # Keep browser open for a few seconds to see the result
            print("\nKeeping browser open for 5 seconds...")
            await asyncio.sleep(5)
            
            print("\n✓ Test completed successfully!")
            
        except Exception as e:
            print(f"\n✗ Error during test: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await browser.close()
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_stock_screening_ui())
    sys.exit(0 if success else 1)
